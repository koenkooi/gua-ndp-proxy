# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
vulnerability reporting: on the repository's **Security** tab choose
**Report a vulnerability**. Include what you found, how to reproduce it, and
the version or commit.

This is a volunteer project. We aim to acknowledge a report within a week and
to agree a fix and disclosure date with you; we will credit you in the release
notes unless you prefer otherwise.

## Supported versions

Only the latest release receives security fixes.

## What this software does with privileges

`gua-ndp-proxy` runs as root (the supplied unit limits it to `CAP_NET_ADMIN`
and `CAP_NET_RAW`). It:

- reads the kernel's neighbor, address and route tables by running `ip`,
- adds and removes `/128` routes tagged `proto 76` and NDP proxy entries on
  the uplink,
- sends ICMPv6 echo requests on downstream links with `ping`.

It runs those programs with an argument list, never through a shell, and
validates interface names before using them. It reads its configuration only
from a file you control and never from the network.

## Threat model

**Downstream links are trusted to be your own devices.** The daemon derives a
device's global address from its link-local neighbor entry, and a device
controls its own link-local address. A malicious or compromised device on a
downstream link can therefore ask to be proxied at any address in your /64.
Mitigations built in:

- an address held by the gateway itself is never proxied,
- an address another host already answers for on the uplink (a live neighbor
  entry there) is never proxied,
- an address covered by someone else's route more specific than the /64 in
  the main table (for example a `/128` towards a VPN peer) is never proxied,
  so the daemon's own `/128` cannot replace or override it,
- one link can claim at most `max_per_interface` addresses, and addresses
  already in use are kept in preference to new ones,
- only routes tagged `proto 76` and proxy entries inside the configured
  prefix (or ones the daemon added) are ever removed.

These are best effort. A quiet LAN host with no neighbor entry on the uplink
can still be impersonated for as long as the device claims its address. Do not
use this on a link where untrusted devices can attach, such as a bridge shared
with a guest network or a public access point.

**Devices get global addresses.** Anything proxied is reachable from the LAN
at a global address, and from the Internet if your upstream router forwards
inbound traffic to the /64. Put a firewall policy on the gateway (the devices'
traffic is forwarded through it) and on the upstream router, and do not
assume the private link hides the device.

**Forwarding is enabled.** The gateway must have IPv6 forwarding on, which
turns it into a router. Check that your firewall rules reflect that.

## The optional USB gadget helper

`usb-gadget-subnet` runs as root from udev when a network interface appears.
It validates the interface name before using it in a path, validates every
value it reads from its root-owned config file and from `resolv.conf`, and
writes the generated file atomically. The interface name is assigned by the
kernel and udev, but predictable naming can embed the device's own MAC
address in it, so a USB device can influence the name; that is why it is
validated rather than trusted.

## Out of scope

- Attacks needing root on the gateway or write access to its configuration.
- Behaviour of the kernel's neighbor discovery, or of systemd-networkd, other
  than how this project drives them.
