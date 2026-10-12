# SPDX-License-Identifier: MIT
import argparse
import ipaddress
import unittest

import gua_ndp_proxy as g

A = ipaddress.IPv6Address
PREFIX = ipaddress.IPv6Network("2001:db8:1234:5678::/64")
LL = A("fe80::200:5eff:fe00:5301")
GUA = A("2001:db8:1234:5678:200:5eff:fe00:5301")


class Derivation(unittest.TestCase):
    def test_derive_gua_keeps_interface_id(self):
        self.assertEqual(g.derive_gua(LL, PREFIX), GUA)

    def test_link_local_is_inverse(self):
        self.assertEqual(g.link_local_for(GUA), LL)

    def test_derive_other_prefix(self):
        other = ipaddress.IPv6Network("2001:db8:aaaa:bbbb::/64")
        self.assertEqual(str(g.derive_gua(LL, other)),
                         "2001:db8:aaaa:bbbb:200:5eff:fe00:5301")


class Parsing(unittest.TestCase):
    def test_neighbors(self):
        text = (
            "fe80::200:5eff:fe00:5301 dev btpan0 lladdr 00:00:5e:00:53:01 REACHABLE\n"
            "fe80::1 dev btpan0 router STALE\n"
            "garbage\n"
            "fe80::2 dev btpan0 FAILED\n"
        )
        got = g.parse_neighbors(text)
        self.assertEqual([(str(a), s) for a, s in got], [
            ("fe80::200:5eff:fe00:5301", "REACHABLE"),
            ("fe80::1", "STALE"),
            ("fe80::2", "FAILED"),
        ])

    def test_proxy_entries(self):
        text = "2001:db8:1234:5678:200:5eff:fe00:5301 proxy\nnot-an-address proxy\n"
        self.assertEqual(g.parse_proxy_entries(text),
                         {"2001:db8:1234:5678:200:5eff:fe00:5301"})

    def test_addresses_with_flags(self):
        text = (
            "2: eth0    inet6 2001:db8:1234:5678::2/64 scope global dynamic "
            "mngtmpaddr noprefixroute \\       valid_lft 3598sec\n"
            "2: eth0    inet6 fe80::1/64 scope link \\       valid_lft forever\n"
            "2: eth0    inet ignored\n"
        )
        got = g.parse_addresses(text)
        self.assertEqual([str(a) for a, _ in got],
                         ["2001:db8:1234:5678::2", "fe80::1"])
        self.assertIn("mngtmpaddr", got[0][1])

    def test_host_routes_bare_and_slash128(self):
        text = (
            "2001:db8:1234:5678:200:5eff:fe00:5301 dev btpan0 metric 1024 pref medium\n"
            "2001:db8:1234:5678:1::1/128 dev usb0 metric 1024\n"
            "2001:db8:1234:5678::/64 dev eth0 proto kernel\n"
            "default via fe80::1 dev eth0\n"
        )
        self.assertEqual(g.parse_host_routes(text), {
            "2001:db8:1234:5678:200:5eff:fe00:5301": "btpan0",
            "2001:db8:1234:5678:1::1": "usb0",
        })

    def test_route_destinations_any_type(self):
        text = (
            "2001:db8:1234:5678::77 dev wg0 metric 1024 pref medium\n"
            "unreachable 2001:db8:1234:5678:1::/80 dev lo metric 1024 pref medium\n"
            "2001:db8:1234:5678:2::/80 proto static metric 1024 pref medium\n"
            "\tnexthop via fe80::1 dev eth0 weight 1\n"
            "default via fe80::1 dev eth0\n"
        )
        self.assertEqual([str(n) for n in g.parse_route_destinations(text)], [
            "2001:db8:1234:5678::77/128",
            "2001:db8:1234:5678:1::/80",
            "2001:db8:1234:5678:2::/80",
        ])


class UplinkPrefix(unittest.TestCase):
    def addrs(self, *items):
        return [(A(a), set(f.split())) for a, f in items]

    def test_picks_global(self):
        got = g.uplink_prefix(self.addrs(
            ("fe80::1", "scope link"),
            ("fd00::5", "scope global"),
            ("2001:db8:1234:5678::2", "scope global dynamic")))
        self.assertEqual(got, PREFIX)

    def test_skips_temporary_and_deprecated(self):
        got = g.uplink_prefix(self.addrs(
            ("2001:db8:1::5", "temporary"),
            ("2001:db8:2::5", "deprecated"),
            ("2001:db8:1234:5678::2", "dynamic")))
        self.assertEqual(got, PREFIX)

    def test_none_without_global(self):
        self.assertIsNone(g.uplink_prefix(self.addrs(("fe80::1", ""), ("fd00::1", ""))))


class Candidates(unittest.TestCase):
    def test_link_local_derives_global(self):
        got = g.candidate_addresses([(LL, "REACHABLE")], PREFIX)
        self.assertEqual(got, {GUA})

    def test_bad_states_ignored(self):
        self.assertEqual(g.candidate_addresses(
            [(LL, "FAILED"), (A("fe80::5"), "INCOMPLETE")], PREFIX), set())

    def test_direct_prefix_entry_kept(self):
        self.assertEqual(g.candidate_addresses([(GUA, "STALE")], PREFIX), {GUA})

    def test_zero_interface_id_skipped(self):
        self.assertEqual(g.candidate_addresses([(A("fe80::"), "STALE")], PREFIX), set())

    def test_foreign_global_ignored(self):
        self.assertEqual(g.candidate_addresses(
            [(A("2001:db8:ffff::1"), "STALE")], PREFIX), set())


class Selection(unittest.TestCase):
    def pick(self, cands, **kw):
        base = dict(local=set(), on_uplink=set(), known=set(), limit=8)
        base.update(kw)
        return [str(a) for a in g.select_addresses(cands, **base)]

    def test_never_proxies_own_address(self):
        self.assertEqual(self.pick({GUA}, local={str(GUA)}), [])

    def test_never_proxies_address_answered_on_uplink(self):
        self.assertEqual(self.pick({GUA}, on_uplink={str(GUA)}), [])

    def test_never_overrides_foreign_route(self):
        foreign = [ipaddress.IPv6Network("2001:db8:1234:5678:200::/72")]
        self.assertEqual(self.pick({GUA}, foreign=foreign), [])

    def test_limit_prefers_known(self):
        cands = {A(f"2001:db8:1234:5678::{i}") for i in range(1, 6)}
        got = self.pick(cands, known={"2001:db8:1234:5678::5"}, limit=2)
        self.assertEqual(len(got), 2)
        self.assertIn("2001:db8:1234:5678::5", got)


class Planning(unittest.TestCase):
    def test_new_address(self):
        p = g.plan({"a": "usb0"}, {}, set(), set())
        self.assertEqual(p.route_set, {"a": "usb0"})
        self.assertEqual(p.proxy_add, {"a"})
        self.assertFalse(p.route_del or p.proxy_del)

    def test_steady_state_is_empty(self):
        p = g.plan({"a": "usb0"}, {"a": "usb0"}, {"a"}, {"a"})
        self.assertTrue(p.empty())

    def test_moved_interface_replaces_route(self):
        p = g.plan({"a": "usb1"}, {"a": "usb0"}, {"a"}, {"a"})
        self.assertEqual(p.route_set, {"a": "usb1"})

    def test_departed_address_removed(self):
        p = g.plan({}, {"a": "usb0"}, {"a"}, {"a"})
        self.assertEqual(p.route_del, {"a": "usb0"})
        self.assertEqual(p.proxy_del, {"a"})

    def test_foreign_proxy_entry_left_alone(self):
        p = g.plan({}, {}, {"foreign"}, set())
        self.assertTrue(p.empty())


class Events(unittest.TestCase):
    def test_uplink_chatter_ignored(self):
        self.assertFalse(g.relevant_events(
            b"fe80::1 dev eth0 lladdr 00:11:22:33:44:55 REACHABLE\n", "eth0"))

    def test_downstream_event_relevant(self):
        self.assertTrue(g.relevant_events(
            b"Deleted fe80::2 dev usb0 lladdr 00:11:22:33:44:55 STALE\n", "eth0"))


class Config(unittest.TestCase):
    def ns(self, **kw):
        base = dict(uplink=None, prefix=None, downstream=None, gadget_driver=None,
                    no_gadget_discovery=False, interval=None,
                    max_per_interface=None, dry_run=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_requires_uplink(self):
        with self.assertRaises(ValueError):
            g.build_config(self.ns(), {})

    def test_cli_overrides_file(self):
        cfg = g.build_config(self.ns(uplink="eth1"), {"uplink": "eth0"})
        self.assertEqual(cfg.uplink, "eth1")

    def test_file_values(self):
        cfg = g.build_config(self.ns(), {
            "uplink": "eth0", "prefix": "2001:db8:1234:5678::/64",
            "downstream": "br0 usb0", "gadget_drivers": "none",
            "interval": "60", "max_per_interface": "3"})
        self.assertEqual(cfg.prefix, PREFIX)
        self.assertEqual(cfg.downstream, ("br0", "usb0"))
        self.assertEqual(cfg.gadget_drivers, ())
        self.assertEqual((cfg.interval, cfg.max_per_interface), (60.0, 3))

    def test_auto_prefix(self):
        self.assertIsNone(g.build_config(self.ns(uplink="eth0"), {}).prefix)

    def test_rejects_non_64_prefix(self):
        with self.assertRaises(ValueError):
            g.build_config(self.ns(uplink="eth0", prefix="2001:db8::/48"), {})

    def test_rejects_bad_interface_name(self):
        with self.assertRaises(ValueError):
            g.build_config(self.ns(uplink="eth0", downstream=["../x"]), {})

    def test_rejects_nothing_to_watch(self):
        with self.assertRaises(ValueError):
            g.build_config(self.ns(uplink="eth0", no_gadget_discovery=True), {})

    def test_rejects_tiny_interval(self):
        with self.assertRaises(ValueError):
            g.build_config(self.ns(uplink="eth0", interval=0.1), {})


if __name__ == "__main__":
    unittest.main()
