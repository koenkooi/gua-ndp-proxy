#!/bin/sh
# SPDX-License-Identifier: MIT
# install-config.sh SOURCE DIR
#
# Meson install script: installs SOURCE into DIR unless a file of that name is
# already there. install_data() would overwrite the admin's edited config on
# every reinstall; a package manager would keep it, so do the same here.
set -eu

src=$1
dir=${DESTDIR:-}$2
dest=$dir/$(basename "$src")

if [ -e "$dest" ]; then
    echo "Keeping existing $dest"
    exit 0
fi
install -d -m 0755 "$dir"
install -m 0644 "$src" "$dest"
echo "Installing $src to $dest"
