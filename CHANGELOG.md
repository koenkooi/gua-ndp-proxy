# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `gua-ndp-proxy` daemon: installs a `/128` route and an NDP proxy entry for
  each device that SLAACs an address in the uplink's /64 on a downstream link.
- `advertise = yes`: keeps the off-link router advertisement on every downstream
  link on the current prefix through systemd-networkd drop-ins, surviving
  generators that rewrite the `.network` file and upstream prefix changes.
- Configuration by file and flags; `--once`, `--dry-run`, automatic
  prefix detection from the uplink, and `uplink = auto` (the default) to
  use the interface carrying the IPv6 default route. A named uplink that
  does not exist stops the daemon with a message listing the ones that do.
- Safeguards: foreign routes untouched, own and uplink-answered addresses
  refused, per-link address cap, cleanup on shutdown.
- Meson build, hardened systemd unit, sysctl drop-in and examples;
  `meson install` adds a default configuration without overwriting an
  existing one.
- Optional udev helper giving each USB gadget interface its own private
  subnet.
- Unit tests, a network-namespace integration test and CI.
