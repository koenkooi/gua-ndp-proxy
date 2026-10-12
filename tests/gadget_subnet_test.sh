#!/bin/bash
# SPDX-License-Identifier: MIT
# Tests contrib/usb-gadget-subnet against a fake sysfs and networkd directory.
# Runs in a throwaway network namespace so host addresses cannot interfere;
# exits 77 (skip) where that is unavailable.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=${GADGET_SCRIPT:-$HERE/../contrib/usb-gadget-subnet/usb-gadget-subnet.sh}

if [[ -z ${GADGET_NETNS:-} ]]; then
    unshare -Urn true 2>/dev/null || { echo "SKIP: cannot create namespace"; exit 77; }
    GADGET_NETNS=1 GADGET_SCRIPT=$SCRIPT exec unshare -Urn "$0" "$@"
fi

fail() { echo "FAIL: $*"; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/sys/usb0" "$work/sys/usb1" "$work/net"
echo 5 > "$work/sys/usb0/ifindex"
echo 85 > "$work/sys/usb1/ifindex"   # same index modulo 80 as usb0

export GADGET_SUBNET_SYSFS=$work/sys GADGET_SUBNET_NETWORKD_DIR=$work/net
export GADGET_SUBNET_NO_RELOAD=1 GADGET_SUBNET_CONF=$work/none.conf

echo "== first device gets the subnet derived from its ifindex"
"$SCRIPT" usb0
grep -q '^Address=192.168.165.1/24$' "$work/net/05-usb-gadget-usb0.network" \
    || fail "unexpected subnet for usb0"
grep -q 'IPv6Prefix' "$work/net/05-usb-gadget-usb0.network" \
    && fail "IPv6 prefix written without GUA_PREFIX"

echo "== a second device with a colliding index gets a different subnet"
"$SCRIPT" usb1
grep -q '^Address=192.168.166.1/24$' "$work/net/05-usb-gadget-usb1.network" \
    || fail "usb1 did not avoid usb0's subnet"

echo "== rerunning for the same interface keeps its subnet"
"$SCRIPT" usb0
grep -q '^Address=192.168.165.1/24$' "$work/net/05-usb-gadget-usb0.network" \
    || fail "usb0 subnet changed on rerun"

echo "== config file adds the off-link prefix"
printf 'GUA_PREFIX="2001:db8:1234:5678::/64"\n' > "$work/gua.conf"
GADGET_SUBNET_CONF=$work/gua.conf "$SCRIPT" usb0
grep -q '^Prefix=2001:db8:1234:5678::/64$' "$work/net/05-usb-gadget-usb0.network" \
    || fail "prefix missing"
grep -q '^OnLink=no$' "$work/net/05-usb-gadget-usb0.network" || fail "OnLink missing"
grep -q '^AddressAutoconfiguration=yes$' "$work/net/05-usb-gadget-usb0.network" \
    || fail "AddressAutoconfiguration missing"

echo "== bad input is rejected"
"$SCRIPT" '../x' 2>/dev/null && fail "path traversal accepted"
printf 'GUA_PREFIX="2001:db8::/48"\n' > "$work/bad.conf"
GADGET_SUBNET_CONF=$work/bad.conf "$SCRIPT" usb0 2>/dev/null && fail "non-/64 accepted"

echo "== unknown interface is ignored"
"$SCRIPT" nosuch0
[[ ! -e $work/net/05-usb-gadget-nosuch0.network ]] || fail "wrote config for unknown interface"

echo "PASS"
