# Configuration

Settings come from, in order of precedence: command-line flags, the config
file, built-in defaults. The config file defaults to
`/etc/gua-ndp-proxy/gua-ndp-proxy.conf` and is optional.

## Config file

INI format, one `[main]` section:

```ini
[main]
uplink = auto
prefix = auto
downstream = br-pan
gadget_drivers = cdc_ether rndis_host cdc_ncm
interval = 30
max_per_interface = 8
```

| Key | Flag | Default | Meaning |
|---|---|---|---|
| `uplink` | `--uplink` | `auto` | Interface facing the LAN, where proxy entries are added, or `auto` for the interface carrying the IPv6 default route (lowest metric; downstream links are never picked) |
| `prefix` | `--prefix` | `auto` | The delegated /64, or `auto` to follow the uplink's global address |
| `downstream` | `--downstream` (repeatable) | none | Interfaces or bridges to watch, space separated in the file |
| `gadget_drivers` | `--gadget-driver` (repeatable) | `cdc_ether rndis_host cdc_subset cdc_eem cdc_ncm zaurus` | Also watch any interface bound to one of these kernel drivers; `none` disables. Every such interface except the uplink is treated as a trusted downstream link, including USB Ethernet adapters or modems that use these drivers |
| | `--no-gadget-discovery` | | Only watch `downstream` |
| `interval` | `--interval` | `30` | Reconcile at least this often (seconds, minimum 2) |
| `max_per_interface` | `--max-per-interface` | `8` | Most addresses one link may claim |
| `advertise` | `--advertise` | `no` | Advertise the prefix on downstream links through systemd-networkd drop-ins; see below |

With `uplink = auto` the daemon waits until a default route exists, which at
boot is when the uplink's first router advertisement arrives. If the route
moves to another interface, it removes what it installed on the old one and
carries on there. A named uplink must exist when the daemon starts; if it
does not, the daemon exits and its log lists the interfaces that do.

Other flags: `--config FILE`, `--once` (reconcile once and leave the state
installed), `--dry-run` (log what would change), `-v`, `--version`.

To find the driver of a new device: `basename $(readlink /sys/class/net/IFACE/device/driver)`.

## System settings

The daemon warns at startup when these are wrong.

| Setting | Why |
|---|---|
| `net.ipv6.conf.all.forwarding=1` | The gateway routes between the uplink and downstream links |
| `net.ipv6.conf.all.proxy_ndp=1` | Without it the proxy entries are never answered. Setting `net.ipv6.conf.<uplink>.proxy_ndp=1` instead also works |
| `net.ipv6.conf.<uplink>.accept_ra=2` | With forwarding on, the kernel ignores router advertisements on an interface whose `accept_ra` is 1, which silently drops the uplink's own SLAAC address and default route |

The installed `60-gua-ndp-proxy.conf` sets the first two. The third depends
on the uplink name, so set it yourself; see
`examples/sysctl-uplink-accept-ra.conf`.

## Advertising the prefix downstream

Each downstream link's router advertisement must carry the uplink's /64 with
the on-link flag clear and the autonomous flag set.

### With `advertise = yes` (recommended on systemd-networkd)

The daemon writes
`/run/systemd/network/<file>.network.d/60-gua-ndp-proxy.conf` for the
`.network` file of every downstream link and runs `networkctl reload` when one
changes. Nothing else needs to know the prefix:

- It follows the prefix the daemon uses, so with `prefix = auto` an upstream
  prefix change updates every link. Devices then SLAAC the new prefix; their
  old address ages out with its lifetime (one hour with networkd's defaults).
- Generators that rewrite the `.network` file, such as a Bluetooth PAN helper,
  cannot remove it.
- List bridges in `downstream`: a bridge is advertised on even with no members,
  so its first client gets the prefix.

The drop-ins are removed when the daemon stops. A reload reconfigures only
links whose configuration changed; a brief interruption on those links is
normal. networkd can drop the daemon's routes on such a link; the daemon puts
them back on its next pass (neighbor events usually trigger one within seconds,
`interval` is the upper bound).

### By hand

With systemd-networkd
(`IPv6Forwarding=` needs version 256 or newer; older versions ignore it, and
the sysctl drop-in enables forwarding anyway):

```ini
[Network]
IPv6SendRA=yes
IPv6Forwarding=yes

[IPv6Prefix]
Prefix=2001:db8:1234:5678::/64
OnLink=no
AddressAutoconfiguration=yes
```

Add it as an extra `[IPv6Prefix]` block next to any ULA prefix you already
advertise there. Anything that generates or rewrites that `.network` file
(a Bluetooth PAN helper, a provisioning tool) must preserve the block; a
generator that does not will silently remove the device's global address the
next time it rewrites the file.

The gateway must not hold an `Address=` inside that /64 on the downstream
link.
