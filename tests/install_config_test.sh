#!/bin/sh
# SPDX-License-Identifier: MIT
# Tests data/install-config.sh: installs the config once and never overwrites
# an existing one, so reinstalling keeps the admin's edits.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=$HERE/../data/install-config.sh
SRC=$HERE/../examples/gua-ndp-proxy.conf

fail() { echo "FAIL: $*"; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
dest=$work/etc/gua-ndp-proxy/gua-ndp-proxy.conf

echo "== first install creates the directory and the file"
DESTDIR=$work sh "$SCRIPT" "$SRC" /etc/gua-ndp-proxy
cmp -s "$SRC" "$dest" || fail "config not installed under DESTDIR"
[ -n "$(find "$dest" -perm 0644)" ] || fail "config is not mode 0644"

echo "== reinstalling keeps an edited config"
echo "uplink = br-lan" >> "$dest"
DESTDIR=$work sh "$SCRIPT" "$SRC" /etc/gua-ndp-proxy
grep -q '^uplink = br-lan$' "$dest" || fail "edited config was overwritten"

echo "PASS"
