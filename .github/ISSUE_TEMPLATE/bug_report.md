---
name: Bug report
about: Report a bug with curfewd screen-time enforcement, locking, or packaging
title: "[Bug] "
labels: bug
---

### Environment
- **curfewd version:** (e.g. `dpkg -l curfewd`)
- **Distro & version:** (e.g. Linux Mint 22.3 Cinnamon, Ubuntu 24.04, Debian 12)
- **Desktop environment & display server:** (e.g. Cinnamon on X11, XFCE on X11, GNOME on Wayland)
- **Display manager:** (e.g. LightDM, GDM3, SDDM)

### What happened?
Describe what you expected vs. what actually happened.

### Relevant logs
Please attach redacted output from:
```bash
sudo journalctl -u timekpr -n 80 --no-pager
sudo curfewctl --userinfo <username>
```
