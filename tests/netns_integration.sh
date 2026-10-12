#!/bin/sh
# SPDX-License-Identifier: MIT
# Runs the daemon against a real kernel inside an unprivileged user+network
# namespace, so no root is needed. Exits 77 (skip) where that is unavailable.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
DAEMON=${GUA_DAEMON:-$HERE/../src/gua_ndp_proxy.py}

if [ -z "${GUA_NETNS:-}" ]; then
    unshare -Urnm true 2>/dev/null || { echo "SKIP: cannot create user+net+mount namespace"; exit 77; }
    command -v ip >/dev/null || { echo "SKIP: iproute2 missing"; exit 77; }
    GUA_NETNS=1 GUA_DAEMON=$DAEMON exec unshare -Urnm "$0" "$@"
fi

# /sys still describes the parent network namespace until it is remounted;
# the daemon discovers links through it.
mount -t sysfs sysfs /sys 2>/dev/null || { echo "SKIP: cannot mount sysfs"; exit 77; }

fail() { echo "FAIL: $*"; exit 1; }
run_daemon() { python3 "$DAEMON" --once --uplink up0 --downstream dn0 \
    --no-gadget-discovery "$@"; }

LL=fe80::200:5eff:fe00:5301
GUA=2001:db8:1234:5678:200:5eff:fe00:5301

ip link set lo up
ip link add up0 type dummy
ip link add dn0 type dummy
ip link set up0 up
ip link set dn0 up
ip -6 addr add 2001:db8:1234:5678::2/64 dev up0 nodad
echo 1 > /proc/sys/net/ipv6/conf/all/forwarding
echo 1 > /proc/sys/net/ipv6/conf/all/proxy_ndp
echo 2 > /proc/sys/net/ipv6/conf/up0/accept_ra

echo "== device appears: route and proxy entry installed"
ip -6 neigh add "$LL" lladdr 00:00:5e:00:53:01 dev dn0 nud permanent
run_daemon
ip -6 route show table main proto 76 | grep -q "^$GUA dev dn0" \
    || fail "host route missing"
ip -6 neigh show proxy dev up0 | grep -q "^$GUA " || fail "proxy entry missing"

echo "== second pass is a no-op"
run_daemon --verbose 2>&1 | grep -q "route $GUA" && fail "route re-applied"

echo "== a route we do not own is never touched"
ip -6 route add 2001:db8:1234:5678:dead::1/128 dev dn0
run_daemon
ip -6 route show table main | grep -q "^2001:db8:1234:5678:dead::1 " \
    || fail "foreign route was removed"
ip -6 route del 2001:db8:1234:5678:dead::1/128 dev dn0

echo "== an address someone else routes elsewhere is refused"
ip link add other0 type dummy
ip link set other0 up
ip -6 route add 2001:db8:1234:5678:200:5eff:fe00:5307/128 dev other0
ip -6 neigh add fe80::200:5eff:fe00:5307 lladdr 00:00:5e:00:53:07 dev dn0 nud permanent
run_daemon
ip -6 route show table main | grep -q "^2001:db8:1234:5678:200:5eff:fe00:5307 dev other0 " \
    || fail "foreign host route was replaced"
ip -6 neigh show proxy dev up0 | grep -q "fe00:5307" && fail "proxied a foreign-routed address"
ip -6 neigh del fe80::200:5eff:fe00:5307 dev dn0
ip link del other0

echo "== device leaves: everything we installed is removed"
ip -6 neigh del "$LL" dev dn0
run_daemon
ip -6 route show table main proto 76 | grep -q "$GUA" && fail "route not removed"
ip -6 neigh show proxy dev up0 | grep -q "$GUA" && fail "proxy entry not removed"

echo "== address already answered on the uplink is refused"
ip -6 neigh add 2001:db8:1234:5678:200:5eff:fe00:5302 lladdr 02:00:00:00:00:01 \
    dev up0 nud permanent
ip -6 neigh add fe80::200:5eff:fe00:5302 lladdr 02:00:00:00:00:02 dev dn0 nud permanent
run_daemon
ip -6 route show table main proto 76 | grep -q "fe00:5302" \
    && fail "proxied an address another host owns"

echo "== our own address is refused"
ip -6 neigh add fe80::2 lladdr 02:00:00:00:00:03 dev dn0 nud permanent
run_daemon
ip -6 route show table main proto 76 | grep -q "5678::2 " \
    && fail "proxied this host's own address"

echo "== dry run changes nothing"
ip -6 neigh replace "$LL" lladdr 00:00:5e:00:53:01 dev dn0 nud permanent
run_daemon --dry-run
ip -6 route show table main proto 76 | grep -q "$GUA" && fail "dry run installed a route"

echo "== daemon mode: reacts to a new device, idles quietly, cleans up on SIGTERM"
ip -6 neigh flush dev dn0 2>/dev/null || true
ip -6 neigh flush dev up0 2>/dev/null || true
python3 "$DAEMON" --uplink up0 --downstream dn0 --no-gadget-discovery &
PID=$!
sleep 1
ip -6 neigh replace "$LL" lladdr 00:00:5e:00:53:01 dev dn0 nud permanent
n=0
until ip -6 route show table main proto 76 | grep -q "^$GUA "; do
    n=$((n + 1)); [ $n -le 15 ] || { kill "$PID"; fail "daemon never installed the route"; }
    sleep 1
done
ticks() { awk '{print $14 + $15}' "/proc/$PID/stat"; }
before=$(ticks)
sleep 8
used=$(( $(ticks) - before ))
echo "daemon CPU over 8s idle: ${used} ticks (100/s)"
[ "$used" -lt 80 ] || { kill "$PID"; fail "daemon is busy-looping"; }
kill -TERM "$PID"
wait "$PID" || fail "daemon exited non-zero on SIGTERM"
ip -6 route show table main proto 76 | grep -q "$GUA" && fail "route survived shutdown"
ip -6 neigh show proxy dev up0 | grep -q "$GUA" && fail "proxy entry survived shutdown"

echo "== daemon adopts state left by an earlier run and removes it on SIGTERM"
run_daemon
ip -6 neigh show proxy dev up0 | grep -q "^$GUA " || fail "--once did not install the proxy entry"
python3 "$DAEMON" --uplink up0 --downstream dn0 --no-gadget-discovery &
PID=$!
sleep 3
kill -TERM "$PID"
wait "$PID" || fail "daemon exited non-zero on SIGTERM"
ip -6 neigh show proxy dev up0 | grep -q "$GUA" && fail "inherited proxy entry survived shutdown"

echo "PASS"
