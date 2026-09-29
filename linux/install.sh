#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/thermal-monitor"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APPS/net.local.thermalmonitor.desktop"

python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' || {
  echo "GTK 4 / PyGObject is required (on Zorin/Ubuntu: python3-gi and gir1.2-gtk-4.0)." >&2
  exit 1
}
python3 -c 'import gi; gi.require_version("Secret", "1"); from gi.repository import Secret' || {
  echo "libsecret introspection is required for secure saved BMC credentials (gir1.2-secret-1)." >&2
  exit 1
}
command -v nvidia-smi >/dev/null || {
  echo "nvidia-smi is missing; install/repair the NVIDIA driver tools first." >&2
  exit 1
}
command -v ipmitool >/dev/null || {
  echo "ipmitool is missing; install it with the Linux package manager." >&2
  exit 1
}

install -d -m 0755 "$DEST" "$APPS"
install -m 0644 "$HERE/monitor_core.py" "$DEST/monitor_core.py"
install -m 0644 "$HERE/credential_store.py" "$DEST/credential_store.py"
install -m 0644 "$HERE/window_geometry.py" "$DEST/window_geometry.py"
install -m 0755 "$HERE/thermal_monitor.py" "$DEST/thermal_monitor.py"
sed "s|@APP_PATH@|$DEST/thermal_monitor.py|g" \
  "$HERE/thermal-monitor.desktop.in" > "$DESKTOP_FILE"
chmod 0644 "$DESKTOP_FILE"
# Migrate the earlier launcher name so GNOME matches the GTK application ID.
if [[ -f "$APPS/thermal-monitor.desktop" ]]; then
  rm -- "$APPS/thermal-monitor.desktop"
fi
if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$APPS"
fi
echo "Installed Thermal Monitor under $DEST"
echo "Launch it from the Applications menu or run: python3 '$DEST/thermal_monitor.py'"
