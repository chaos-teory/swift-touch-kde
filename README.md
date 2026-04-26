# swift-touch-kde

Linux/KDE toolkit for Acer Swift SFG16-74 multimedia touchpad reverse-engineering and control.

Current hardware focus:
- `PIXA480A:00 093A:480A` (touchpad)
- `1025174B:00 1025:174B` (Acer vendor HID channel, `hidraw1` on this host)

The tool uses raw HID ioctls (`HIDIOCGFEATURE/HIDIOCSFEATURE/HIDIOCGINPUT`) and can:
- scan candidate HID devices;
- send raw feature commands;
- run predefined Acer touchpad commands found in AcerSense binaries;
- read/write MediaTouchPad registers (`118..123`) via Windows-compatible report `0x43`;
- monitor feature reports and input events for state changes.

## File

- `swift_touch_kde.py` - CLI tool / daemon entrypoint.
- `swift_touch_kde_tray.py` - KDE tray controller.
- `swift_touch_kde_settings.py` - KDE settings window (GUI).
- `install_kde_app.sh` - installer (udev + systemd + desktop integration).

## Run

```bash
cd /home/chaos/git/swift-touch-kde
chmod +x swift_touch_kde.py
sudo ./swift_touch_kde.py scan
```

## KDE App + Persistent Service

Install persistent integration (udev + user systemd service + KDE tray app + autostart):

```bash
cd /home/chaos/git/swift-touch-kde
chmod +x install_kde_app.sh
sudo ./install_kde_app.sh chaos
```

After install:
- media daemon service: `swift-touch-media.service` (user service)
- tray app launcher: `Swift Touch KDE`
- settings app launcher: `Swift Touch KDE Settings`
- autostart entry: `~/.config/autostart/swift-touch-kde-tray.desktop`
- System Settings search alias entry: `kcm_swift_touch.desktop`

Open settings window manually:

```bash
~/.local/bin/swift-touch-kde-settings.py
```

## Main commands

1. Scan devices:

```bash
sudo ./swift_touch_kde.py scan
```

2. Monitor feature reports (`a0` and `0b`) for changes:

```bash
sudo ./swift_touch_kde.py monitor --seconds 30
```

3. Send legacy Acer A0 touchpad commands (vendor HID channel):

```bash
sudo ./swift_touch_kde.py touchpad-query
sudo ./swift_touch_kde.py touchpad-enable
sudo ./swift_touch_kde.py touchpad-disable
sudo ./swift_touch_kde.py touchpad-set --value 0x02
```

4. Raw HID read/write:

```bash
sudo ./swift_touch_kde.py raw-get --dev /dev/hidraw1 --kind feature --report 0xa0 --length 65
sudo ./swift_touch_kde.py raw-set --dev /dev/hidraw1 --length 65 --bytes "a0 00 a0 04 00 02 00 00 02"
sudo ./swift_touch_kde.py a0-send --cmd "a0 00 a0 00 00 01 00 01"
```

5. MediaTouchPad register control (working path on SFG16-74: `/dev/hidraw0`, report `0x43`):

```bash
sudo ./swift_touch_kde.py mtp-dump --dev /dev/hidraw0
sudo ./swift_touch_kde.py mtp-read --dev /dev/hidraw0 --reg 120
sudo ./swift_touch_kde.py mtp-write --dev /dev/hidraw0 --reg 120 --value 1
sudo ./swift_touch_kde.py set-touchpad-status-in-media-mode --dev /dev/hidraw0 --status 1
sudo ./swift_touch_kde.py set-app-auto-control-mode --dev /dev/hidraw0 --mode 0
```

6. Capture evdev keys while touching multimedia icons:

```bash
sudo ./swift_touch_kde.py watch-events --seconds 30
```

7. Unified raw capture to file (best for reverse sessions):

```bash
sudo ./swift_touch_kde.py probe --seconds 45 --log /tmp/swift-touch-probe.log
```

8. Live hidraw packet sniff with timestamps:

```bash
sudo ./swift_touch_kde.py sniff-hidraw --dev /dev/hidraw0 --seconds 30 --only-changes --log /tmp/hidraw0-sniff.log
```

9. Decode multimedia consumer reports (report `0x0c`) from touchpad:

```bash
sudo ./swift_touch_kde.py watch-consumer --seconds 30 --only-press --log /tmp/touch-consumer.log
```

10. Emit real media keys (`PlayPause`, `Previous`, `Next`) from multimedia strip taps:

```bash
sudo ./swift_touch_kde.py run-media-keys --dev /dev/hidraw0 --seconds 0
```

Debug mode (no key injection, only prints detected actions):

```bash
sudo ./swift_touch_kde.py run-media-keys --dev /dev/hidraw0 --emit print --seconds 30
```

Known usage codes observed on this device:
- `0x00e9` -> `VOLUMEUP`
- `0x00ea` -> `VOLUMEDOWN`
- release event -> `0x0000`

Additional Acer reverse commands:

```bash
sudo ./swift_touch_kde.py appstatus-on
sudo ./swift_touch_kde.py appstatus-off
```

## Notes

- Commands are experimental and intentionally conservative.
- If `--dev` is omitted, the tool auto-picks Acer vendor HID (`VID 1025`) first.
- For MediaTouchPad settings (`GET/SET_*_IN_MEDIA_MODE`) prefer explicit `--dev /dev/hidraw0` on this host.
- `FUNCTION_KEY_CONTROL_EN` (`reg 118`) may auto-override `MEDIA_TP_FUNCTION_CONTROL` (`reg 120`) depending on current mode logic.
- If you hit bad state, power-cycle is still the safest full reset.

## Windows Reverse Notes

Latest static reverse snapshot is documented here:

- `docs/windows_reverse_acer5.md`

Highlights:
- `AQAUserPS.exe` processes RawInput touchpad events and reports to `AcerQAAgent.exe`.
- Media-touchpad service commands were recovered (`GET/SET_*` for media mode, touchpad-in-media-mode, lighting, brightness, YouTube button mode).
- YouTube previous/next icon behavior has 3 modes (`SpeedDownUp`, `RewindForward`, `LastNextVideo`), selected via `Mode=1..3`.
