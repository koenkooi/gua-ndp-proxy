---
name: Bug report
about: Something does not work as documented
labels: bug
---

**What happened, and what did you expect?**

**Setup**
- Distribution and kernel (`uname -r`):
- systemd version:
- Python version:
- Downstream link type (USB gadget driver, bridge, ...):
- Device OS and how it generates its IPv6 address:

**Output**

<!-- `journalctl -u gua-ndp-proxy`, and on the gateway:
     ip -6 route show table main proto 76
     ip -6 neigh show proxy
     ip -6 neigh show dev <downstream>
     On the device: ip -6 addr
     Please redact addresses you consider private. -->

**Not a security issue?** Security problems go through the private reporting
described in SECURITY.md, not here.
