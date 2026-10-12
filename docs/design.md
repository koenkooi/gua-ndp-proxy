# Design

## The problem

A gateway has a wired uplink in a globally routed IPv6 /64 and one or more
downstream links to small devices: a USB network gadget, a Bluetooth PAN
bridge, a point-to-point tunnel. Each downstream link is private, not a
segment of the LAN.

The devices should be reachable from the rest of the LAN at a real IPv6
address. The obvious options do not fit:

- **NAT** works outbound, but nothing on the LAN can reach the device
  without per-service port forwarding.
- **Bridging the link onto the LAN** is not possible: it is a private link to
  one device, not something the uplink's switch can extend into.
- **Routing a delegated sub-prefix** per link needs DHCPv6-PD or a /56 to
  hand out, which many networks do not offer, and the uplink router must be
  taught the route.

## The approach

1. **Advertise the uplink's /64 downstream, off-link.** The gateway sends
   router advertisements on each downstream link with a prefix option for the
   same /64 the uplink is in, flagged `OnLink=no, Autonomous=yes`. The device
   SLAACs a global address in that prefix but does not assume the rest of the
   /64 is on its link, so everything else still goes through the gateway as
   its default router. The gateway itself holds no address in that prefix on
   the downstream link, so it does not grow a bogus on-link route for the whole
   /64.

2. **Install a host route and an NDP proxy entry per device.** For every
   device address it finds, this daemon adds a `/128` route towards the
   downstream interface and an `ip -6 neigh add proxy` entry on the uplink.
   Other LAN hosts then resolve the device's address to the gateway's MAC and
   the gateway forwards to the device.

3. **Find the address without waiting for it to show up.** The gateway
   routes the device off-link, so it never performs neighbor discovery for the
   device's global address: that address does not appear in the neighbor
   table. The device's link-local address does appear (it is needed to reach
   the default router). With EUI-64 SLAAC the two share one interface
   identifier, so the global address is derived from the link-local one.

## Keeping it working

- **Idle links.** The kernel can garbage-collect the neighbor entry for a
  quiet link entirely, which would drop the proxy even though the device's
  address is still valid. Each pass pings the link-local address of every
  proxied device, and the daemon remembers the last addresses it saw on a link
  until the link goes away.
- **Reconnects.** A fresh interface starts with an empty neighbor table and
  nothing guarantees the device speaks first, so each pass also pings
  `ff02::1` on every downstream link.
- **Event storms.** The daemon's own pings cause neighbor events. Events on
  the uplink are ignored and reconciles are at least two seconds apart, so it
  does not feed itself.
- **Bridges.** A bridge outlives its clients, so a bridge only counts as
  present while it has member ports.

## What it will and will not do

- It only manages routes tagged `proto 99` and proxy entries inside the
  configured prefix (or ones it added itself). Other routes are left alone.
- It never proxies an address this host holds, an address another host
  already answers for on the uplink, or more than `max_per_interface`
  addresses per link. See [SECURITY.md](../SECURITY.md) for why.
- On SIGTERM it removes everything it installed.

## Limitations

- **EUI-64 only.** Devices that use RFC 7217 stable-privacy or random
  interface identifiers for their global address end up with an identifier
  that differs from their link-local one, so the derived address is wrong and
  the device is not proxied. Configure the device so both addresses use the
  same identifier, or give it a static address inside the prefix, which is
  picked up directly if it appears in the neighbor table. On a device running
  systemd-networkd, `IPv6LinkLocalAddressGenerationMode=` in `[Network]` sets
  how the link-local address is built and `Token=` in `[IPv6AcceptRA]` sets
  the identifier used for SLAAC addresses (see `systemd.network(5)`).
- **Static advertisement.** The daemon does not change what is advertised
  downstream. If the upstream prefix changes, the `[IPv6Prefix]` blocks on
  the downstream links must be updated by whatever manages them. With
  `prefix = auto` the daemon follows the uplink, but devices keep
  addresses from the old prefix until the advertisement changes. It logs an
  error when a configured prefix no longer matches the uplink.
- **IPv6 only, one /64.** There is no IPv4 handling and no prefix delegation.
- **Trusted downstream links.** See the threat model in
  [SECURITY.md](../SECURITY.md).
