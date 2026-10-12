# Contributing

Bug reports, fixes, documentation and ideas are welcome. For anything larger
than a small fix, open an issue first so we can agree on the approach.
Security problems go through [SECURITY.md](SECURITY.md), not public issues.

## Development setup

```sh
meson setup build
meson test -C build --print-errorlogs
```

`meson test` runs three suites:

- `unit`: pure-logic tests of the daemon; no privileges needed.
- `netns-integration`: runs the daemon against a real kernel inside an
  unprivileged user, network and mount namespace. Skipped (not failed) where
  unprivileged user namespaces are unavailable.
- `gadget-subnet`: tests the optional udev helper against a fake sysfs.

For quick iteration on the daemon:
`PYTHONPATH=src python3 -m unittest discover -s tests`.

## Style

- Python 3.9 compatible, standard library only. Lint with
  `ruff check src tests` (config in `ruff.toml`).
- Shell scripts pass `shellcheck`.
- Keep parsing and decision logic in pure functions that take text and return
  data, so they can be unit-tested; keep `ip` and `ping` calls in the thin
  `Proxy` layer.
- Comments explain why, not what.

## Changes

- Add or update tests with the change, and update the docs in the same
  commit.
- One logical change per commit, with a message that says why.
- Describe in the pull request how you tested it, and on what kind of link
  (USB gadget, bridge, ...) if you ran it on hardware.

By contributing you agree your work is licensed under the MIT license.

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
