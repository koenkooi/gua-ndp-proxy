# Installation

Requirements on the gateway: Linux, Python 3.9 or newer (standard library
only), `ip` from iproute2, `ping` from iputils, and systemd for the supplied
unit. Building needs Meson 0.61 or newer and Ninja.

## Build and install

```sh
meson setup build
meson test -C build
sudo meson install -C build
```

Pass `--prefix=/usr` to install under `/usr`. The systemd unit and sysctl
directories are taken from pkg-config (`systemd.pc`) when it is available and
default to `PREFIX/lib/systemd/system` and `PREFIX/lib/sysctl.d` otherwise;
override them with `-Dsystemdsystemunitdir=` and `-Dsysctldir=`. Use
`-Dsysctl=false` to skip the sysctl file. Packagers can stage with
`meson install -C build --destdir DIR`.

`meson test` runs the unit tests and a network-namespace integration test.
The integration test needs unprivileged user namespaces and is skipped where
they are unavailable.

## Configure

1. Advertise the uplink's /64 off-link on each downstream link, as described
   in [configuration](configuration.md#advertising-the-prefix-downstream)
   (`examples/downstream-link.network`).
2. Set `net.ipv6.conf.<uplink>.accept_ra = 2`
   (`examples/sysctl-uplink-accept-ra.conf`) and apply sysctls with
   `sudo sysctl --system`.
3. Create `SYSCONFDIR/gua-ndp-proxy/gua-ndp-proxy.conf` from
   `examples/gua-ndp-proxy.conf`. The unit is skipped (its
   `ConditionPathExists=` fails) without it. `SYSCONFDIR` is Meson's
   `sysconfdir`: `/etc` with `--prefix=/usr`, but `/usr/local/etc` with the
   default prefix; pass `-Dsysconfdir=/etc` to use `/etc` there too.

## Run

```sh
sudo systemctl enable --now gua-ndp-proxy
journalctl -u gua-ndp-proxy -f
```

Try it without changing anything first with
`gua-ndp-proxy --config SYSCONFDIR/gua-ndp-proxy/gua-ndp-proxy.conf --dry-run --once -v`.
Run by hand without `--config`, the daemon reads
`/etc/gua-ndp-proxy/gua-ndp-proxy.conf` if it exists.

## Notes on the unit

The unit drops all capabilities except `CAP_NET_ADMIN` and `CAP_NET_RAW` and
applies systemd's filesystem, kernel and system-call sandboxing. It has been
checked with `systemd-analyze verify` only; if the daemon fails to start on
your system, `journalctl -u gua-ndp-proxy` shows why, and relaxing
`SystemCallFilter=` is the first thing to try. Please report what you found.

## Uninstall

```sh
sudo systemctl disable --now gua-ndp-proxy
sudo ninja -C build uninstall
```

The daemon removes its routes and proxy entries when it stops.
