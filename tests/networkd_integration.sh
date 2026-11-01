#!/bin/sh
# SPDX-License-Identifier: MIT
# Runs the daemon with --advertise against a real systemd-networkd in an
# unprivileged namespace: a veth peer must SLAC the advertised prefix, and a
# prefix change on the uplink must reach it. Exits 77 (skip) where unavailable.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
DAEMON=${GUA_DAEMON:-$HERE/../src/gua_ndp_proxy.py}
NETWORKD=${GUA_NETWORKD:-/usr/lib/systemd/systemd-networkd}

if [ -z "${GUA_NETNS:-}" ]; then
    [ -x "$NETWORKD" ] || { echo "SKIP: no systemd-networkd"; exit 77; }
    command -v networkctl >/dev/null || { echo "SKIP: no networkctl"; exit 77; }
    unshare -Urnm true 2>/dev/null || { echo "SKIP: cannot create namespace"; exit 77; }
    GUA_NETNS=1 GUA_DAEMON=$DAEMON GUA_NETWORKD=$NETWORKD exec unshare -Urnm "$0" "$@"
fi

skip() { echo "SKIP: $*"; exit 77; }
fail() { echo "FAIL: $*"; cat "$WORK/networkd.log" 2>/dev/null | tail -n 20; exit 1; }

# Read-only /sys tells networkd there is no udev to wait for, as in a container.
mount -t sysfs -o ro sysfs /sys 2>/dev/null || skip "cannot mount sysfs"
mount -t tmpfs tmpfs /run || skip "cannot mount tmpfs on /run"
umask 022
WORK=$(mktemp -d)
mkdir -p /run/systemd/network /run/systemd/netif/links /run/systemd/netif/leases \
    /run/systemd/netif/lldp /run/netns "$WORK/etc-network"

# Only uid 0 is mapped here, so networkd's unprivileged user must be uid 0 too.
sed -e 's/^systemd-network:[^:]*:[0-9]*:[0-9]*:/systemd-network:x:0:0:/' /etc/passwd > "$WORK/passwd"
sed -e 's/^systemd-network:[^:]*:[0-9]*:/systemd-network:x:0:/' /etc/group > "$WORK/group"
mount --bind "$WORK/passwd" /etc/passwd
mount --bind "$WORK/group" /etc/group
mount --bind "$WORK/etc-network" /etc/systemd/network 2>/dev/null || skip "cannot bind /etc/systemd/network"

P1=2001:db8:1234:5678
P2=2001:db8:aaaa:bbbb
ip link set lo up
ip link add up0 type dummy
ip link set up0 up
ip -6 addr add "$P1::2/64" dev up0 nodad
echo 1 > /proc/sys/net/ipv6/conf/all/forwarding
echo 1 > /proc/sys/net/ipv6/conf/all/proxy_ndp
echo 2 > /proc/sys/net/ipv6/conf/up0/accept_ra

ip netns add dev
ip link add dn0 type veth peer name eth0 netns dev
ip netns exec dev sh -c 'echo 2 > /proc/sys/net/ipv6/conf/eth0/accept_ra'
ip netns exec dev ip link set lo up
ip netns exec dev ip link set eth0 address 00:00:5e:00:53:21
ip netns exec dev ip link set eth0 up

# A main file with only a ULA prefix, as a generator like a PAN helper writes.
cat > /etc/systemd/network/25-dn0.network <<NET
[Match]
Name=dn0

[Network]
Address=fd00:db8::1/64
IPv6SendRA=yes

[IPv6Prefix]
Prefix=fd00:db8::/64
NET

SYSTEMD_LOG_LEVEL=info "$NETWORKD" > "$WORK/networkd.log" 2>&1 &
ND=$!
trap 'kill $ND 2>/dev/null || true; rm -rf "$WORK"' EXIT

# networkctl needs the system bus, which does not exist here; networkd also
# reloads on SIGHUP, which is what its bus call ends up doing.
mkdir "$WORK/bin"
printf '#!/bin/sh\nkill -HUP %s\n' "$ND" > "$WORK/bin/networkctl"
chmod +x "$WORK/bin/networkctl"
PATH=$WORK/bin:$PATH

n=0
until grep -q "^NETWORK_FILE=" "/run/systemd/netif/links/$(cat /sys/class/net/dn0/ifindex)" 2>/dev/null; do
    n=$((n + 1))
    kill -0 $ND 2>/dev/null || { tail -n 5 "$WORK/networkd.log"; skip "networkd did not start here"; }
    [ $n -le 20 ] || { tail -n 5 "$WORK/networkd.log"; skip "networkd never matched the link"; }
    sleep 1
done
ip -6 addr show dev up0 | grep -q "$P1::2" || ip -6 addr add "$P1::2/64" dev up0 nodad

device_has() {
    n=0
    until ip netns exec dev ip -6 -o addr show dev eth0 | grep -q "$1:200:5eff:fe00:5321"; do
        n=$((n + 1)); [ $n -le "${2:-40}" ] || return 1
        sleep 1
    done
}

run_daemon() { python3 "$DAEMON" --once --advertise --uplink up0 --downstream dn0 \
    --no-gadget-discovery; }

echo "== no global address before the daemon advertises it"
device_has "$P1" 3 && fail "device already has the global prefix"

echo "== drop-in advertises the uplink prefix; device gets a global address"
run_daemon
DROP=/run/systemd/network/25-dn0.network.d/60-gua-ndp-proxy.conf
grep -q "^Prefix=$P1::/64$" "$DROP" || fail "drop-in missing"
device_has "$P1" || fail "device did not SLAC $P1"
ip netns exec dev ip -6 -o addr show dev eth0 | grep -q "fd00:db8:0:0:200:5eff:fe00:5321\|fd00:db8::200:5eff:fe00:5321" \
    || fail "main file's ULA prefix was lost"
ip netns exec dev ip -6 route show | grep -q "^$P1::/64" && fail "prefix must be off-link on the device"

echo "== a generator rewriting the main file does not lose the prefix"
sed -i 's/^Address=.*/Address=fd00:db8::1\/64\n# rewritten/' /etc/systemd/network/25-dn0.network
networkctl reload
run_daemon
grep -q "^Prefix=$P1::/64$" "$DROP" || fail "drop-in lost"

echo "== uplink prefix change reaches the device"
ip -6 addr del "$P1::2/64" dev up0
ip -6 addr add "$P2::2/64" dev up0 nodad
run_daemon
grep -q "^Prefix=$P2::/64$" "$DROP" || fail "drop-in did not follow the uplink"
device_has "$P2" || fail "device did not SLAC the new prefix"

echo "== daemon mode: a reload that flushes the route is healed, stop cleans up"
python3 "$DAEMON" --advertise --uplink up0 --downstream dn0 --no-gadget-discovery &
PID=$!
n=0
until ip -6 route show table main proto 76 | grep -q "^$P2:200:5eff:fe00:5321 dev dn0"; do
    n=$((n + 1)); [ $n -le 15 ] || { kill "$PID"; fail "route never installed"; }
    sleep 1
done
sleep 3
echo "# rewritten again" >> /etc/systemd/network/25-dn0.network
kill -HUP "$ND"
sleep 2
n=0
until ip -6 route show table main proto 76 | grep -q "^$P2:200:5eff:fe00:5321 dev dn0"; do
    n=$((n + 1)); [ $n -le 12 ] || { kill "$PID"; fail "route not restored after reload"; }
    sleep 1
done
kill -TERM "$PID"
wait "$PID" || fail "daemon exited non-zero on SIGTERM"
[ ! -e "$DROP" ] || fail "drop-in survived shutdown"
ip -6 route show table main proto 76 | grep -q "$P2" && fail "route survived shutdown"

echo "PASS"
