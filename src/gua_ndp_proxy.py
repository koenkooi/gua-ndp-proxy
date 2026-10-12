#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Make devices behind a private link reachable at their own global IPv6 address.

A gateway advertises its delegated /64 to downstream devices (USB gadget,
Bluetooth PAN, ...) with OnLink=no, so each device SLAACs a real address but
still routes through the gateway. This daemon finds those devices and, for
each one, installs a /128 host route towards the downstream link and an NDP
proxy entry on the uplink so the rest of the LAN can reach it.
"""
import argparse
import configparser
import dataclasses
import ipaddress
import logging
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

VERSION = "0.1.0"

log = logging.getLogger("gua-ndp-proxy")

DEFAULT_CONFIG = "/etc/gua-ndp-proxy/gua-ndp-proxy.conf"
DEFAULT_GADGET_DRIVERS = (
    "cdc_ether", "rndis_host", "cdc_subset", "cdc_eem", "cdc_ncm", "zaurus",
)
SYSFS_NET = "/sys/class/net"
PROC_SYS = "/proc/sys"

# Tags the host routes this daemon installs so it never touches anyone else's.
# Must not be a protocol number the kernel assigns (rtnetlink.h RTPROT_*): 99
# is RTPROT_OPENR, so cleanup would have deleted Open/R's routes.
ROUTE_PROTO = "76"

BAD_NEIGH_STATES = frozenset({"FAILED", "INCOMPLETE"})
UNIQUE_LOCAL = ipaddress.IPv6Network("fc00::/7")
IID_MASK = (1 << 64) - 1
IFNAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")

# Our own pings generate neighbor events; without a minimum gap every
# reconcile would trigger the next one.
MIN_RECONCILE_GAP = 2.0
DRIFT_WARN_EVERY = 600.0


# --- pure helpers (no I/O, unit-tested) -------------------------------------

def interface_id(addr: ipaddress.IPv6Address) -> int:
    return int(addr) & IID_MASK


def derive_gua(link_local: ipaddress.IPv6Address,
               prefix: ipaddress.IPv6Network) -> ipaddress.IPv6Address:
    """The address a device SLAACs in `prefix`, given its link-local one.

    Only valid for devices whose global and link-local addresses share an
    interface identifier (EUI-64, or any token-based scheme that does).
    """
    return ipaddress.IPv6Address(int(prefix.network_address) | interface_id(link_local))


def link_local_for(addr: ipaddress.IPv6Address) -> ipaddress.IPv6Address:
    return ipaddress.IPv6Address((0xFE80 << 112) | interface_id(addr))


def parse_neighbors(text: str) -> List[Tuple[ipaddress.IPv6Address, str]]:
    """(address, state) pairs from `ip -6 neigh show`."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            addr = ipaddress.IPv6Address(parts[0].split("%")[0])
        except ValueError:
            continue
        out.append((addr, parts[-1]))
    return out


def parse_proxy_entries(text: str) -> Set[str]:
    """Addresses from `ip -6 neigh show proxy`."""
    found = set()
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        try:
            found.add(str(ipaddress.IPv6Address(parts[0].split("%")[0])))
        except ValueError:
            continue
    return found


def parse_addresses(text: str) -> List[Tuple[ipaddress.IPv6Address, Set[str]]]:
    """(address, flag tokens) from `ip -6 -o addr show`."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet6":
            continue
        try:
            addr = ipaddress.IPv6Address(parts[3].split("/")[0])
        except ValueError:
            continue
        out.append((addr, set(parts[4:])))
    return out


def parse_host_routes(text: str) -> Dict[str, str]:
    """address -> device for /128 routes in `ip -6 route show` output.

    iproute2 prints a host route as a bare address, so a destination with a
    different prefix length is the only thing to skip.
    """
    routes = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or "dev" not in parts:
            continue
        dest = parts[0]
        if dest.endswith("/128"):
            dest = dest[:-4]
        elif "/" in dest:
            continue
        try:
            addr = ipaddress.IPv6Address(dest)
        except ValueError:
            continue
        routes[str(addr)] = parts[parts.index("dev") + 1]
    return routes


ROUTE_TYPES = frozenset({"unicast", "unreachable", "blackhole", "prohibit", "throw"})


def parse_route_destinations(text: str) -> List[ipaddress.IPv6Network]:
    """Destination prefixes in `ip -6 route show` output, of any route type."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] in ROUTE_TYPES:
            parts = parts[1:]
        if not parts:
            continue
        try:
            out.append(ipaddress.IPv6Network(parts[0]))
        except ValueError:
            continue
    return out


def uplink_prefix(addresses: Iterable[Tuple[ipaddress.IPv6Address, Set[str]]]
                  ) -> Optional[ipaddress.IPv6Network]:
    """The /64 the uplink currently holds a usable global address in."""
    for addr, flags in addresses:
        if (addr.is_link_local or addr.is_multicast or addr.is_loopback
                or addr in UNIQUE_LOCAL):
            continue
        if flags & {"temporary", "deprecated", "tentative"}:
            continue
        return ipaddress.IPv6Network((int(addr) & ~IID_MASK, 64))
    return None


def candidate_addresses(neighbors: Iterable[Tuple[ipaddress.IPv6Address, str]],
                        prefix: ipaddress.IPv6Network) -> Set[ipaddress.IPv6Address]:
    """Addresses a downstream device should be reachable at.

    The device's global address almost never appears in this host's neighbor
    table: it routes off-link through us, so we never resolve it. Its
    link-local address does appear, so derive the global one from that.
    """
    found = set()
    for addr, state in neighbors:
        if state in BAD_NEIGH_STATES:
            continue
        if addr in prefix:
            found.add(addr)
        elif addr.is_link_local and interface_id(addr) != 0:
            found.add(derive_gua(addr, prefix))
    return found


def select_addresses(candidates: Iterable[ipaddress.IPv6Address], *,
                     local: Set[str], on_uplink: Set[str], known: Set[str],
                     limit: int,
                     foreign: Iterable[ipaddress.IPv6Network] = ()
                     ) -> List[ipaddress.IPv6Address]:
    """Drop addresses that must never be proxied, then cap the count.

    A downstream device can fabricate neighbor entries, so refuse addresses
    this host owns, that another host already answers for on the uplink, or
    that someone else's route more specific than the /64 already sends
    elsewhere (our /128 would override or replace it), and cap how many one
    link may claim (already-known ones win).
    """
    foreign = list(foreign)
    ok = [a for a in candidates
          if str(a) not in local and str(a) not in on_uplink
          and not any(a in net for net in foreign)]
    ok.sort(key=lambda a: (str(a) not in known, a))
    if len(ok) > limit:
        log.warning("downstream link offers %d addresses, keeping %d", len(ok), limit)
        ok = ok[:limit]
    return ok


@dataclasses.dataclass
class Plan:
    route_set: Dict[str, str]
    route_del: Dict[str, str]
    proxy_add: Set[str]
    proxy_del: Set[str]

    def empty(self) -> bool:
        return not (self.route_set or self.route_del or self.proxy_add or self.proxy_del)


def plan(wanted: Dict[str, str], have_routes: Dict[str, str],
         have_proxy: Set[str], owned_proxy: Set[str]) -> Plan:
    """Diff the desired state against the kernel's.

    `owned_proxy` is the subset of `have_proxy` this daemon may delete.
    """
    return Plan(
        route_set={a: i for a, i in wanted.items() if have_routes.get(a) != i},
        route_del={a: i for a, i in have_routes.items() if a not in wanted},
        proxy_add={a for a in wanted if a not in have_proxy},
        proxy_del={a for a in owned_proxy if a not in wanted},
    )


# --- configuration -----------------------------------------------------------

@dataclasses.dataclass
class Config:
    uplink: str
    prefix: Optional[ipaddress.IPv6Network] = None  # None: follow the uplink
    downstream: Tuple[str, ...] = ()
    gadget_drivers: Tuple[str, ...] = DEFAULT_GADGET_DRIVERS
    interval: float = 30.0
    max_per_interface: int = 8
    dry_run: bool = False


def valid_ifname(name: str) -> bool:
    return bool(IFNAME_RE.match(name))


def parse_prefix(value: str) -> Optional[ipaddress.IPv6Network]:
    if value.strip().lower() in ("", "auto"):
        return None
    net = ipaddress.IPv6Network(value.strip())
    if net.prefixlen != 64:
        raise ValueError("prefix must be a /64 (SLAAC needs 64 interface-id bits)")
    return net


def load_config_file(path: str) -> Dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    with open(path, encoding="utf-8") as fh:
        parser.read_file(fh)
    return dict(parser["main"]) if parser.has_section("main") else {}


def build_config(args: argparse.Namespace, file_values: Dict[str, str]) -> Config:
    """Merge CLI over config file over defaults; raises ValueError on bad input."""
    def pick(cli, key, default):
        if cli is not None:
            return cli
        return file_values.get(key, default)

    uplink = pick(args.uplink, "uplink", None)
    if not uplink:
        raise ValueError("an uplink interface is required (--uplink or config file)")

    downstream = args.downstream if args.downstream else file_values.get(
        "downstream", "").split()
    if args.no_gadget_discovery:
        drivers: Tuple[str, ...] = ()
    elif args.gadget_driver:
        drivers = tuple(args.gadget_driver)
    elif "gadget_drivers" in file_values:
        raw = file_values["gadget_drivers"].split()
        drivers = () if raw == ["none"] else tuple(raw)
    else:
        drivers = DEFAULT_GADGET_DRIVERS

    cfg = Config(
        uplink=uplink,
        prefix=parse_prefix(str(pick(args.prefix, "prefix", "auto"))),
        downstream=tuple(downstream),
        gadget_drivers=drivers,
        interval=float(pick(args.interval, "interval", 30.0)),
        max_per_interface=int(pick(args.max_per_interface, "max_per_interface", 8)),
        dry_run=args.dry_run,
    )
    for name in (cfg.uplink, *cfg.downstream):
        if not valid_ifname(name):
            raise ValueError(f"invalid interface name: {name!r}")
    if not cfg.downstream and not cfg.gadget_drivers:
        raise ValueError("no downstream interfaces: set downstream or enable "
                         "gadget driver discovery")
    if cfg.interval < MIN_RECONCILE_GAP:
        raise ValueError(f"interval must be at least {MIN_RECONCILE_GAP:g}s")
    if cfg.max_per_interface < 1:
        raise ValueError("max_per_interface must be at least 1")
    return cfg


# --- system access -----------------------------------------------------------

def run(argv: List[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("%s: %s", " ".join(argv), exc)
        return subprocess.CompletedProcess(argv, 255, "", str(exc))


class QueryFailed(Exception):
    pass


def query(argv: List[str]) -> str:
    """Output of a read-only `ip` command.

    Raises instead of returning empty output: an empty address or neighbor
    list would silently disable the refusal checks for that pass.
    """
    result = run(argv)
    if result.returncode != 0:
        raise QueryFailed(f"{' '.join(argv)}: {result.stderr.strip()}")
    return result.stdout


def read_sysctl(key: str) -> Optional[str]:
    try:
        with open(os.path.join(PROC_SYS, key), encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        return None


def check_sysctls(uplink: str) -> None:
    for key in ("net/ipv6/conf/all/forwarding", "net/ipv6/conf/all/proxy_ndp"):
        if read_sysctl(key) != "1":
            log.warning("sysctl %s is not 1; proxying will not work",
                        key.replace("/", "."))
    forwarding = read_sysctl(f"net/ipv6/conf/{uplink}/forwarding")
    accept_ra = read_sysctl(f"net/ipv6/conf/{uplink}/accept_ra")
    if forwarding == "1" and accept_ra == "1":
        log.warning("%s: forwarding is on but accept_ra=1, so the kernel ignores "
                    "router advertisements there; set accept_ra=2", uplink)


def gadget_interfaces(drivers: Iterable[str]) -> Set[str]:
    wanted = set(drivers)
    found = set()
    try:
        entries = os.listdir(SYSFS_NET)
    except OSError:
        return found
    for entry in entries:
        try:
            driver = os.path.basename(
                os.readlink(os.path.join(SYSFS_NET, entry, "device", "driver")))
        except OSError:
            continue
        if driver in wanted:
            found.add(entry)
    return found


def link_present(name: str) -> bool:
    """Whether a downstream link currently has something attached.

    A plain interface (USB gadget) disappears when unplugged. A bridge
    outlives its clients, so it only counts while it has member ports.
    """
    base = os.path.join(SYSFS_NET, name)
    if not os.path.isdir(base):
        return False
    if os.path.isdir(os.path.join(base, "bridge")):
        try:
            return bool(os.listdir(os.path.join(base, "brif")))
        except OSError:
            return False
    return True


# --- daemon ------------------------------------------------------------------

class Proxy:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        # iface -> addresses last seen there. Bridges the gaps when an idle
        # link's neighbor entry is garbage-collected; deliberately not
        # persisted so a restart re-derives everything from the kernel.
        self.last_known: Dict[str, List[ipaddress.IPv6Address]] = {}
        self.owned_proxy: Set[str] = set()
        self._prefix: Optional[ipaddress.IPv6Network] = None
        self._drift_warned = 0.0

    def _apply(self, argv: List[str]) -> bool:
        if self.cfg.dry_run:
            log.info("dry-run: %s", " ".join(argv))
            return False
        result = run(argv)
        if result.returncode != 0:
            log.warning("%s failed: %s", " ".join(argv), result.stderr.strip())
            return False
        return True

    def _ping(self, target: str, iface: str) -> None:
        run(["ping", "-6", "-c", "1", "-W", "1", "-I", iface, target])

    def resolve_prefix(self) -> Optional[ipaddress.IPv6Network]:
        live = uplink_prefix(parse_addresses(
            run(["ip", "-6", "-o", "addr", "show", "dev", self.cfg.uplink]).stdout))
        if self.cfg.prefix is None:
            if live is None:
                log.warning("%s has no global prefix; leaving state untouched",
                            self.cfg.uplink)
                return None
            if live != self._prefix:
                log.info("using prefix %s from %s", live, self.cfg.uplink)
                self._prefix = live
            return live
        if live is not None and live != self.cfg.prefix:
            now = time.monotonic()
            if now - self._drift_warned >= DRIFT_WARN_EVERY:
                log.error("configured prefix %s but %s is in %s: the upstream "
                          "prefix changed; update the config and every "
                          "[IPv6Prefix] block advertised downstream",
                          self.cfg.prefix, self.cfg.uplink, live)
                self._drift_warned = now
        return self.cfg.prefix

    def present_links(self) -> List[str]:
        names = set(self.cfg.downstream)
        names |= gadget_interfaces(self.cfg.gadget_drivers)
        names.discard(self.cfg.uplink)
        return sorted(n for n in names if valid_ifname(n) and link_present(n))

    def reconcile(self) -> None:
        try:
            self._reconcile()
        except QueryFailed as exc:
            log.error("skipping this pass: %s", exc)

    def _reconcile(self) -> None:
        prefix = self.resolve_prefix()
        if prefix is None:
            return
        uplink = self.cfg.uplink
        local = {str(a) for a, _ in parse_addresses(
            query(["ip", "-6", "-o", "addr", "show"]))}
        have_routes = parse_host_routes(query(
            ["ip", "-6", "route", "show", "table", "main", "proto", ROUTE_PROTO]))
        foreign = [net for net in parse_route_destinations(
            query(["ip", "-6", "route", "show", "table", "main"]))
            if net.prefixlen > 64 and net.overlaps(prefix)
            and not (net.prefixlen == 128 and str(net.network_address) in have_routes)]
        have_proxy = parse_proxy_entries(query(
            ["ip", "-6", "neigh", "show", "proxy", "dev", uplink]))

        for addr, iface in have_routes.items():
            if addr in have_proxy:
                self._ping(str(link_local_for(ipaddress.IPv6Address(addr))), iface)

        links = self.present_links()
        for iface in links:
            self._ping("ff02::1", iface)

        on_uplink = {str(a) for a, s in parse_neighbors(query(
            ["ip", "-6", "neigh", "show", "dev", uplink]))
            if s not in BAD_NEIGH_STATES}

        claims: Dict[str, List[str]] = {}
        for iface in links:
            neighbors = parse_neighbors(run(
                ["ip", "-6", "neigh", "show", "dev", iface]).stdout)
            candidates = candidate_addresses(neighbors, prefix)
            if not candidates:
                # Remembered addresses go through the same refusals as fresh
                # ones: a LAN host may have claimed one since, or the prefix
                # may have moved.
                candidates = {a for a in self.last_known.get(iface, ()) if a in prefix}
            chosen = select_addresses(
                candidates,
                local=local, on_uplink=on_uplink, known=set(have_routes),
                limit=self.cfg.max_per_interface, foreign=foreign)
            self.last_known[iface] = chosen
            for addr in chosen:
                claims.setdefault(str(addr), []).append(iface)
        wanted: Dict[str, str] = {}
        for key, ifaces in claims.items():
            # The link that already has the route keeps it, so a device
            # cannot take over another link's address just by sorting first.
            current = have_routes.get(key)
            wanted[key] = current if current in ifaces else ifaces[0]
            if len(ifaces) > 1:
                log.warning("%s offered on %s; keeping %s",
                            key, " and ".join(ifaces), wanted[key])
        for iface in list(self.last_known):
            if iface not in links:
                del self.last_known[iface]

        # Entries left by a previous run (--once, a crash) are never re-added,
        # so adopt them here or cleanup() would leave them behind.
        self.owned_proxy |= have_proxy & wanted.keys()
        manageable = {a for a in have_proxy
                      if a in self.owned_proxy
                      or ipaddress.IPv6Address(a) in prefix}
        todo = plan(wanted, have_routes, have_proxy, manageable)
        if todo.empty():
            return
        for addr, iface in sorted(todo.route_set.items()):
            if self._apply(["ip", "-6", "route", "replace", f"{addr}/128",
                            "dev", iface, "proto", ROUTE_PROTO]):
                log.info("route %s via %s", addr, iface)
        for addr in sorted(todo.proxy_add):
            if self._apply(["ip", "-6", "neigh", "replace", "proxy", addr,
                            "dev", uplink]):
                self.owned_proxy.add(addr)
                log.info("proxying %s on %s", addr, uplink)
        for addr, iface in sorted(todo.route_del.items()):
            if self._apply(["ip", "-6", "route", "del", f"{addr}/128",
                            "dev", iface, "proto", ROUTE_PROTO]):
                log.info("removed route %s", addr)
        for addr in sorted(todo.proxy_del):
            if self._apply(["ip", "-6", "neigh", "del", "proxy", addr,
                            "dev", uplink]):
                self.owned_proxy.discard(addr)
                log.info("stopped proxying %s", addr)

    def cleanup(self) -> None:
        """Remove everything this daemon installed."""
        have_routes = parse_host_routes(run(
            ["ip", "-6", "route", "show", "table", "main", "proto", ROUTE_PROTO]).stdout)
        for addr, iface in have_routes.items():
            self._apply(["ip", "-6", "route", "del", f"{addr}/128",
                         "dev", iface, "proto", ROUTE_PROTO])
        for addr in sorted(self.owned_proxy):
            self._apply(["ip", "-6", "neigh", "del", "proxy", addr,
                         "dev", self.cfg.uplink])
        self.owned_proxy.clear()


def start_monitor() -> subprocess.Popen:
    return subprocess.Popen(["ip", "-6", "monitor", "neigh"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            bufsize=0)


def relevant_events(chunk: bytes, uplink: str) -> bool:
    """Whether a neighbor-event chunk touches anything but the uplink.

    LAN chatter on the uplink is constant and irrelevant here.
    """
    for line in chunk.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if "dev" in parts:
            idx = parts.index("dev") + 1
            if idx < len(parts) and parts[idx] != uplink:
                return True
    return False


def monitor_loop(proxy: Proxy, stop: threading.Event) -> None:
    mon = start_monitor()
    last = 0.0
    pending = True
    try:
        while not stop.is_set():
            if mon.poll() is not None:
                log.warning("ip monitor exited, restarting")
                if stop.wait(2):
                    break
                mon = start_monitor()
                pending = True
                continue
            ready, _, _ = select.select([mon.stdout], [], [], 1.0)
            if ready:
                chunk = os.read(mon.stdout.fileno(), 65536)
                if chunk and relevant_events(chunk, proxy.cfg.uplink):
                    pending = True
            now = time.monotonic()
            due = (pending and now - last >= MIN_RECONCILE_GAP) \
                or now - last >= proxy.cfg.interval
            if not due:
                continue
            try:
                proxy.reconcile()
            except Exception:
                log.exception("reconcile failed")
            last = time.monotonic()
            pending = False
            while select.select([mon.stdout], [], [], 0)[0]:
                if not os.read(mon.stdout.fileno(), 65536):
                    break
    finally:
        mon.terminate()
        try:
            mon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mon.kill()


# --- entry point -------------------------------------------------------------

class SdFormatter(logging.Formatter):
    """Prefix messages with syslog priorities so journald levels them."""
    LEVELS = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4,
              logging.ERROR: 3, logging.CRITICAL: 2}

    def format(self, record: logging.LogRecord) -> str:
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return f"<{self.LEVELS.get(record.levelno, 6)}>{text}"


def setup_logging(verbose: bool) -> None:
    handler = logging.StreamHandler(sys.stderr)
    if os.environ.get("JOURNAL_STREAM"):
        handler.setFormatter(SdFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gua-ndp-proxy",
        description="Proxy global IPv6 addresses of devices on private downstream "
                    "links onto the uplink.")
    p.add_argument("--config", metavar="FILE",
                   help=f"config file (default: {DEFAULT_CONFIG} if present)")
    p.add_argument("--uplink", metavar="IFACE", help="interface facing the LAN")
    p.add_argument("--prefix", metavar="CIDR|auto",
                   help="delegated /64; 'auto' (default) follows the uplink")
    p.add_argument("--downstream", metavar="IFACE", action="append",
                   help="downstream interface or bridge (repeatable)")
    p.add_argument("--gadget-driver", metavar="DRIVER", action="append",
                   help="also watch interfaces bound to this kernel driver "
                        "(repeatable; replaces the default list)")
    p.add_argument("--no-gadget-discovery", action="store_true",
                   help="only watch --downstream interfaces")
    p.add_argument("--interval", type=float, metavar="SECS",
                   help="full reconcile at least this often (default 30)")
    p.add_argument("--max-per-interface", type=int, metavar="N",
                   help="cap on addresses one link may claim (default 8)")
    p.add_argument("--once", action="store_true",
                   help="reconcile once and exit, leaving state installed")
    p.add_argument("--dry-run", action="store_true",
                   help="log the changes instead of making them")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=VERSION)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = make_parser().parse_args(argv)
    setup_logging(args.verbose)

    file_values: Dict[str, str] = {}
    config_path = args.config
    if config_path is None and os.path.exists(DEFAULT_CONFIG):
        config_path = DEFAULT_CONFIG
    try:
        if config_path:
            file_values = load_config_file(config_path)
        cfg = build_config(args, file_values)
    except (OSError, configparser.Error, ValueError) as exc:
        log.error("configuration error: %s", exc)
        return 2

    check_sysctls(cfg.uplink)
    proxy = Proxy(cfg)

    if args.once:
        proxy.reconcile()
        return 0

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    log.info("starting: uplink=%s prefix=%s", cfg.uplink, cfg.prefix or "auto")
    monitor_loop(proxy, stop)
    if not cfg.dry_run:
        proxy.cleanup()
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
