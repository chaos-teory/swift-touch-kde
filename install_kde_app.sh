#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./install_kde_app.sh [username]" >&2
  exit 1
fi

USER_NAME="${1:-chaos}"
USER_ID="$(id -u "${USER_NAME}")"
USER_HOME="$(getent passwd "${USER_NAME}" | cut -d: -f6)"
REPO_DIR="${USER_HOME}/git/swift-touch-kde"

if [[ ! -f "${REPO_DIR}/swift_touch_kde.py" ]]; then
  echo "Project not found: ${REPO_DIR}" >&2
  exit 1
fi

echo "[1/6] Install udev rules..."
cat > /etc/udev/rules.d/85-swift-touch-kde.rules <<EOF
KERNEL=="uinput", OWNER="${USER_NAME}", GROUP="${USER_NAME}", MODE="0660", OPTIONS+="static_node=uinput"
SUBSYSTEM=="hidraw", KERNEL=="hidraw*", KERNELS=="*093A:480A*", OWNER="${USER_NAME}", GROUP="${USER_NAME}", MODE="0660"
EOF
chmod 0644 /etc/udev/rules.d/85-swift-touch-kde.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw || true
udevadm trigger --name-match=uinput || true

echo "[2/6] Install user systemd service..."
install -d -m 0755 "${USER_HOME}/.config/systemd/user"
install -m 0644 \
  "${REPO_DIR}/packaging/systemd/swift-touch-media.service" \
  "${USER_HOME}/.config/systemd/user/swift-touch-media.service"

echo "[3/7] Install KDE launchers..."
install -d -m 0755 "${USER_HOME}/.local/bin"
install -m 0755 \
  "${REPO_DIR}/swift_touch_kde_tray.py" \
  "${USER_HOME}/.local/bin/swift-touch-kde-tray.py"
install -m 0755 \
  "${REPO_DIR}/swift_touch_kde_settings.py" \
  "${USER_HOME}/.local/bin/swift-touch-kde-settings.py"

echo "[4/7] Install desktop entries..."
install -d -m 0755 "${USER_HOME}/.local/share/applications" "${USER_HOME}/.config/autostart"
cat > "${USER_HOME}/.local/share/applications/swift-touch-kde-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Swift Touch KDE
Comment=Swift SFG16 multimedia touchpad integration
Exec=${USER_HOME}/.local/bin/swift-touch-kde-tray.py
Icon=input-touchpad
Terminal=false
Categories=Qt;KDE;Utility;Settings;
OnlyShowIn=KDE;
StartupNotify=false
EOF
cp -f \
  "${USER_HOME}/.local/share/applications/swift-touch-kde-tray.desktop" \
  "${USER_HOME}/.config/autostart/swift-touch-kde-tray.desktop"

sed "s|__USER_HOME__|${USER_HOME}|g" \
  "${REPO_DIR}/packaging/desktop/swift-touch-kde-settings.desktop" \
  > "${USER_HOME}/.local/share/applications/swift-touch-kde-settings.desktop"

sed "s|__USER_HOME__|${USER_HOME}|g" \
  "${REPO_DIR}/packaging/desktop/kcm_swift_touch.desktop" \
  > "${USER_HOME}/.local/share/applications/kcm_swift_touch.desktop"

echo "[5/7] Update desktop database..."
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${USER_HOME}/.local/share/applications" || true
fi

echo "[6/7] Fix ownership..."
chown -R "${USER_NAME}:${USER_NAME}" \
  "${USER_HOME}/.config/systemd/user/swift-touch-media.service" \
  "${USER_HOME}/.local/bin/swift-touch-kde-tray.py" \
  "${USER_HOME}/.local/bin/swift-touch-kde-settings.py" \
  "${USER_HOME}/.local/share/applications/swift-touch-kde-tray.desktop" \
  "${USER_HOME}/.local/share/applications/swift-touch-kde-settings.desktop" \
  "${USER_HOME}/.local/share/applications/kcm_swift_touch.desktop" \
  "${USER_HOME}/.config/autostart/swift-touch-kde-tray.desktop"

echo "[7/7] Enable and start service..."
sudo -u "${USER_NAME}" XDG_RUNTIME_DIR="/run/user/${USER_ID}" systemctl --user daemon-reload
sudo -u "${USER_NAME}" XDG_RUNTIME_DIR="/run/user/${USER_ID}" systemctl --user enable --now swift-touch-media.service

echo
echo "Installed successfully for user: ${USER_NAME}"
echo "Service: swift-touch-media.service (user)"
echo "Tray app desktop entry: ${USER_HOME}/.local/share/applications/swift-touch-kde-tray.desktop"
echo "Settings app desktop entry: ${USER_HOME}/.local/share/applications/swift-touch-kde-settings.desktop"
