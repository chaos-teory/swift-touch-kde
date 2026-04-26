# swift-touch-kde

Acer Swift multimedia touchpad integration for KDE Plasma on Linux.

This project provides:
- media-strip event decoding for supported Acer Swift touchpads;
- media key emission (`Play/Pause`, `Previous`, `Next`);
- KDE tray app and settings window;
- KDE System Settings integration;
- Debian package (`.deb`) build and install workflow.

## Status

Current support is focused on:
- `Acer Swift SFG16-74`
- touchpad HID: `PIXA480A:00 093A:480A`

The project is functional but still experimental. Behavior on other models is not guaranteed.

## Install (Recommended)

Install from GitHub Releases:

1. Open latest release:
   - `https://github.com/chaos-teory/swift-touch-kde/releases/latest`
2. Download:
   - `swift-touch-kde_<version>_all.deb`
   - `swift-touch-kde_<version>_all.deb.sha256`
3. Install package:

```bash
sudo apt install ./swift-touch-kde_*_all.deb
```

4. Run user setup (as your regular user, not root):

```bash
swift-touch-kde-user-setup
```

After install:
- command-line tool: `swift-touch-kde`
- GUI settings: `swift-touch-kde-settings`
- tray app: `swift-touch-kde-tray`
- user service: `swift-touch-media.service`
- KDE System Settings module: `Swift Touch KDE`

## Build Debian Package

Build locally:

```bash
sudo apt install -y debhelper dh-python python3-all
cd swift-touch-kde
dpkg-buildpackage -us -uc -b
```

Install built package:

```bash
sudo apt install ../swift-touch-kde_*_all.deb
swift-touch-kde-user-setup
```

## Developer Install (From Source)

If you want to run directly from source instead of `.deb`:

```bash
git clone https://github.com/chaos-teory/swift-touch-kde.git
mkdir -p ~/git
mv swift-touch-kde ~/git/
cd ~/git/swift-touch-kde
sudo ./install_kde_app.sh "$USER"
```

Note: `install_kde_app.sh` expects repository path `~/git/swift-touch-kde` for the target user.

## Usage

Show all commands:

```bash
swift-touch-kde --help
```

Common commands:

```bash
# scan devices
sudo swift-touch-kde scan

# run daemon mode (debug print)
sudo swift-touch-kde run-media-keys --dev /dev/hidraw0 --emit print --seconds 30

# read media-mode register
sudo swift-touch-kde mtp-read --dev /dev/hidraw0 --reg 120

# watch consumer reports
sudo swift-touch-kde watch-consumer --seconds 30 --only-press
```

Service control:

```bash
systemctl --user status swift-touch-media.service
systemctl --user restart swift-touch-media.service
journalctl --user -u swift-touch-media.service -f
```

## Troubleshooting

- If service cannot access `/dev/hidraw0` or `/dev/uinput`, run:

```bash
swift-touch-kde-user-setup
```

- If touchpad enters a broken hardware state, a full power cycle may be required.

- Check current service logs:

```bash
journalctl --user -u swift-touch-media.service -n 120 --no-pager
```

## Uninstall

```bash
sudo apt purge swift-touch-kde
```

## Reverse Engineering Notes

Windows reverse notes are documented in:
- `docs/windows_reverse_acer5.md`
