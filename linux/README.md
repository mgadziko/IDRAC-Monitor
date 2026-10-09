# Thermal Monitor for Linux

This is a separate Linux implementation of Thermal Monitor. It is developed in
the existing repository without changing the Windows application. It displays
NVIDIA GPU temperature, power, utilization, memory, and BMC fan/thermal sensors;
fan-curve control is opt-in.
GPU telemetry and fan-curve decisions refresh every 10 seconds; BMC queries are
limited to every 30 seconds and back off progressively after errors such as
RMCP+ session exhaustion.

Each GPU card shows numeric readings plus live bars for temperature, power,
utilization, and VRAM. Temperature bars are amber from 75 °C and red above
80 °C; power warns at 90% of the reported limit and turns red above it;
utilization warns at 80%; VRAM warns at 85% and turns red if it exceeds the
reported capacity. The bars are visual indicators only and never control GPUs.
The BMC CPU1 and CPU2 temperatures appear above the inlet and exhaust readings,
respectively, within the **Environmental temperatures** section. CPU bars use a
0–100 °C scale; inlet/exhaust bars use a 0–80 °C scale. Those four readings are
omitted from the detailed sensor list to avoid duplication.
The window opens at 920×790 with vertical scrolling for smaller displays. On
X11 desktops it saves and restores its screen position in the user settings.
GTK4 on Wayland does not permit applications to set or query top-level window
positions, so that feature is unavailable there. The
fan RPM readings appear in a dark panel to the right of the checkboxes, below
the credentials and Refresh button. Fans are ordered 1, 3, 5 above 2, 4, 6.
CPU1/CPU2 and inlet/exhaust temperatures are displayed in the dedicated bars;
other generic BMC temperature sensors are omitted.

## WhiteLotus target

- Zorin OS 18, x86_64
- NVIDIA driver with `nvidia-smi` (currently sees two Tesla P40 GPUs)
- GTK 4 / PyGObject and `ipmitool` (present on the target at initial inspection)
- WhiteLotus BMC: `192.168.4.122`

On WhiteLotus, launch from a terminal in this directory:

```sh
python3 thermal_monitor.py
```

Enter the BMC password in the app to read BMC sensors. The app never writes the
password to its settings file or passes it on the command line. When
“Remember password securely in the desktop keyring” is checked, it saves the
password to the signed-in desktop's Secret Service (GNOME Keyring) after a
successful BMC query; unchecking the option removes the saved keyring entry.
The BMC address, account name, and remember preference are stored in the
user-only settings file. The keyring must be unlocked in the signed-in desktop
session.

The **Refresh now** button starts an immediate BMC query even during automatic
poll backoff. A spinner/status message appears while it connects. GPU polling
continues independently every 10 seconds, so a slow BMC response does not hold
up the GPU readings.

BMC inlet/exhaust meters are shown below GPU telemetry; fan readings are in the
dark panel beside the checkboxes. The curve has eight tiers: the six Windows-default points
`0/3000`, `60/4000`, `65/5000`, `70/6000`, `75/7000`, `80/8000`, followed by
`85/8700` and `90/9360` (°C/RPM). The curve editor validates ascending
temperatures and 2800–9360 RPM targets;
**Apply Settings** saves the curve. **Start Fan Control** enables iDRAC manual
mode and applies the matching target duty using the calibration and tier/hysteresis
behavior from the Windows app. The controller raises tiers immediately and
requires three cool samples at least 4 °C below the previous tier threshold to
lower a tier. **Stop (leave speed)** stops curve updates without sending another
fan command. When you quit, the app explicitly offers **Restore iDRAC Automatic
Control**, **Keep current manual fan speed**, or **Don't quit**. Selecting
restore sends the Dell automatic-control command; a failed restore leaves the
app open so it cannot accidentally exit while manual control remains in place.
Use the startup checkbox only if you explicitly want the saved curve applied at desktop login.
“Launch app at desktop login” creates a per-user desktop autostart entry.
Each temperature/RPM value pair is visually enclosed together; the tier title
and field labels remain outside the outline.
Curve tiers are arranged in two rows: 1, 3, 5, 7 above 2, 4, 6, 8. These are still
temperature-to-target-RPM curve points, not separate per-fan controls.

## Install as a user desktop app

```sh
./install.sh
```

This installs the Python sources under `~/.local/share/thermal-monitor` and adds
a launcher under `~/.local/share/applications`, which can be searched for in
the Applications menu and pinned to the GNOME/Zorin dock. The WhiteLotus
desktop installer also adds it to the current user's dock favorites. It does
not require root or modify system packages.

## Development checks

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile monitor_core.py thermal_monitor.py credential_store.py
```

The UI requires GTK 4 and PyGObject. Telemetry uses Linux `nvidia-smi` and
`ipmitool`; no Python packages are installed by the app.

## Fan control behavior and caution

Starting fan control sends the Dell raw IPMI command to enable manual mode,
then sets all fan zones to the duty corresponding to the hottest GPU's selected
curve tier. This is a hardware control, not a read-only action. **Stop (leave
speed)** intentionally retains the current manual duty. A normal quit requires
an explicit choice to restore iDRAC automatic control or keep that duty; a crash
or forced termination cannot present the prompt and may leave manual mode in
place. GPU-local temperature/power comes from
`nvidia-smi`; BMC readings are chassis sensors and must not be mislabeled as GPU
temperatures.
