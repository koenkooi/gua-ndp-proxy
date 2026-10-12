# Troubleshooting

Check the daemon first: `journalctl -u gua-ndp-proxy`. It logs every route and
proxy entry it adds or removes. `gua-ndp-proxy --dry-run -v --once` shows what
it would do without changing anything.

On the gateway, the working state for one device looks like this:

```
$ ip -6 route show table main proto 76
2001:db8:1234:5678:200:5eff:fe00:5301 dev usb0 metric 1024 pref medium
$ ip -6 neigh show proxy dev eth0
2001:db8:1234:5678:200:5eff:fe00:5301 proxy
```

| Symptom | Likely cause |
|---|---|
| Device has only a link-local (and ULA) address, no global one | The downstream advertisement does not carry the `/64` prefix option. Check the `.network` file for the second `[IPv6Prefix]` block, and whether a tool rewrote it |
| Device has a global address but there is no route or proxy entry | The device's global address does not match the one derived from its link-local address (stable-privacy or random identifiers; see [design](design.md#limitations)). Compare the two on the device |
| Route and proxy entry exist, LAN hosts get no reply | `net.ipv6.conf.all.proxy_ndp` or `forwarding` is 0 (the daemon warns at startup) |
| Gateway lost its own IPv6 default route after enabling forwarding | Uplink `accept_ra` is 1; set it to 2 |
| Log says the configured prefix does not match the uplink | The upstream prefix changed. Update `prefix` and the advertised `[IPv6Prefix]` blocks, or use `prefix = auto` for the daemon and update the advertisements |
| Address works, then disappears after the device sits idle | Should not happen: each pass pings proxied devices. If it does, check the interval and that `ping` is installed |
| Nothing is proxied and the log says "offers N addresses, keeping M" | The link claimed more addresses than `max_per_interface`; a device (or something spoofing one) is flooding neighbor entries |
| An address is skipped silently | It is held by this host, or another host already answers for it on the uplink |

Requirements on the gateway: `ip` (iproute2), `ping` (iputils), and
`CAP_NET_ADMIN` and `CAP_NET_RAW`.
