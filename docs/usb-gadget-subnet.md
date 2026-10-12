# USB gadget subnet helper

Optional. When several USB gadget devices can be attached at once, giving
each the same private IPv4 subnet makes their routes collide and only one
works. This helper is run by udev for every new gadget network interface and
writes a systemd-networkd config for it with its own private `/24`
(`192.168.<n>.0/24`), a DHCP server for the device and, if configured, the
off-link router advertisement `gua-ndp-proxy` needs.

Enable it at build time with `-Dgadget-subnet=true`. It installs:

- `usb-gadget-subnet` into `PREFIX/libexec/gua-ndp-proxy/`
- `99-gua-usb-gadget-subnet.rules` into udev's rules directory (from
  `udev.pc`, or `-Dudevrulesdir=`)
- `gadget-subnet.conf.example` among the examples

Copy that example to `/etc/gua-ndp-proxy/gadget-subnet.conf`, set
`GUA_PREFIX` to your delegated /64, then replug the device (or
`udevadm trigger --action=add /sys/class/net/<iface>`).

## How the subnet is chosen

Starting from `SUBNET_BASE + ifindex mod SUBNET_SPAN`, the first subnet not
already used by another live interface or by another live gadget interface's
config file. The result is written to
`/etc/systemd/network/05-usb-gadget-<iface>.network`, which sorts ahead of
generic matches. Two devices plugged in at the same instant can still race
for the same subnet before either config exists.

## Matching other devices

The udev rule matches the kernel drivers `cdc_ether`, `rndis_host`,
`cdc_subset`, `cdc_eem`, `cdc_ncm` and `zaurus`. For another driver, find it
with `basename $(readlink /sys/class/net/IFACE/device/driver)` and add it to
the rule.

## Safety

The interface name is validated before it is used in a path, resolvers are
validated as IPv4 addresses before they are written into the config, and the
file is written atomically so networkd never reads a partial one.
