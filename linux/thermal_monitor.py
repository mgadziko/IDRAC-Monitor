#!/usr/bin/env python3
"""GTK4 desktop UI for Linux GPU/BMC telemetry and opt-in fan control."""

from __future__ import annotations

import configparser
import os
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# The target desktop is driven by an ASPEED BMC graphics adapter. This UI is
# lightweight and does not benefit from GPU rendering; use GTK's software path.
os.environ.setdefault("GSK_RENDERER", "cairo")

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

import credential_store
from window_geometry import X11WindowGeometry
from monitor_core import (
    DEFAULT_FAN_RPMS,
    DEFAULT_FAN_TEMPERATURES,
    bmc_poll_due,
    desired_fan_rpm,
    duty_for_rpm,
    fan_control_autostart_ready,
    fan_sensor_grid_position,
    gpu_bar_state,
    parse_temperature_c,
    query_gpus,
    query_ipmi,
    set_ipmi_fan_control,
    validate_fan_curve,
)


DEFAULT_BMC_HOST = "192.168.4.122"
DEFAULT_BMC_USER = "root"
POLL_SECONDS = 10
# iDRAC has a small RMCP+ session pool.  Thirty seconds keeps the live
# telemetry useful without repeatedly competing for a session.
BMC_POLL_SECONDS = 30
BMC_MAX_BACKOFF_SECONDS = 300
GPU_TEMPERATURE_WARNING_C = 75
GPU_TEMPERATURE_CRITICAL_C = 80
GPU_POWER_WARNING_FRACTION = 0.90
GPU_UTILIZATION_WARNING_PERCENT = 80
GPU_MEMORY_WARNING_FRACTION = 0.85
BMC_TEMPERATURE_BAR_MAX_C = 80
CONFIG_FILE = Path.home() / ".config" / "thermal-monitor" / "settings.ini"
AUTOSTART_FILE = Path.home() / ".config" / "autostart" / "thermal-monitor.desktop"
APP_SCRIPT = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "thermal-monitor" / "thermal_monitor.py"


def display_number(value: Any, suffix: str = "") -> str:
    return "—" if value is None else f"{value:g}{suffix}"


class ThermalMonitor(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="net.local.thermalmonitor")
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="telemetry")
        self.gpu_refresh_lock = threading.Lock()
        self.bmc_refresh_lock = threading.Lock()
        self.fan_control_lock = threading.Lock()
        self.fan_control_active = False
        self.fan_control_inflight = False
        self.fan_control_autostart_attempted = False
        self.latest_gpus: list[dict[str, Any]] = []
        self.fan_temperatures: list[int] = list(DEFAULT_FAN_TEMPERATURES)
        self.fan_rpms: list[int] = list(DEFAULT_FAN_RPMS)
        self.fan_curve_autosave_source: int | None = None
        self.target_fan_rpm = 0
        self.cool_samples = 0
        self.applied_fan_duty: int | None = None
        self.password = ""
        self.gpu_rows: list[dict[str, dict[str, Gtk.Widget]]] = []
        self.bmc_temperature_rows: dict[str, tuple[Gtk.Label, Gtk.ProgressBar]] = {}
        self.saved_settings: tuple[str, str, bool, bool, bool] | None = None
        self.saved_window_position: tuple[int, int] | None = None
        self.window_position_candidate: tuple[int, int] | None = None
        self.window_position_candidate_since = 0.0
        try:
            self.window_geometry: X11WindowGeometry | None = X11WindowGeometry()
        except OSError:
            self.window_geometry = None
        self.next_bmc_attempt = 0.0
        self.bmc_backoff_seconds = 60
        self.bmc_status = "BMC sensors have not been read yet."
        self.bmc_failed = False
        self.gpu_error: str | None = None
        self.gpu_status = "waiting for first sample"
        self.keyring_available = credential_store.is_available()

    def do_activate(self) -> None:
        hostname = socket.gethostname()
        machine_name = {"whitelotus": "WhiteLotus"}.get(
            hostname.casefold(), hostname
        )
        self.window_title = f"Dell iDRAC 8 Thermal Monitor: {machine_name}"
        self.window = Gtk.ApplicationWindow(application=self, title=self.window_title)
        self.window.set_default_size(920, 790)
        self._build_ui()
        self.window.connect("close-request", self._on_window_close_request)
        self.window.present()
        GLib.timeout_add(350, self._restore_window_position)
        GLib.timeout_add_seconds(1, self._poll_window_position)
        self.refresh()
        self._restore_saved_password()
        GLib.timeout_add_seconds(POLL_SECONDS, self._poll)

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        root.set_margin_top(8)
        root.set_margin_bottom(8)
        root.set_margin_start(18)
        root.set_margin_end(18)
        content_scroller = Gtk.ScrolledWindow()
        content_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content_scroller.set_child(root)
        self.window.set_child(content_scroller)

        heading = Gtk.Label(label=self.window_title)
        heading.add_css_class("title-1")
        heading.set_xalign(0)
        root.append(heading)

        gpu_frame = Gtk.Frame(label="NVIDIA GPU telemetry")
        root.append(gpu_frame)
        self.gpu_grid = Gtk.Grid(column_spacing=16, row_spacing=8)
        self.gpu_grid.set_margin_top(6)
        self.gpu_grid.set_margin_bottom(6)
        self.gpu_grid.set_margin_start(12)
        self.gpu_grid.set_margin_end(12)
        gpu_frame.set_child(self.gpu_grid)

        bmc_temperature_frame = Gtk.Frame(
            label=f"Inlet and exhaust temperatures (bar scale: 0–{BMC_TEMPERATURE_BAR_MAX_C} °C)"
        )
        bmc_temperature_grid = Gtk.Grid(column_spacing=18, row_spacing=6)
        bmc_temperature_grid.set_margin_top(6)
        bmc_temperature_grid.set_margin_bottom(6)
        bmc_temperature_grid.set_margin_start(12)
        bmc_temperature_grid.set_margin_end(12)
        for column, (key, title) in enumerate(
            (("Inlet Temp", "Inlet"), ("Exhaust Temp", "Exhaust"))
        ):
            panel = Gtk.Grid(column_spacing=8, row_spacing=5)
            panel.set_hexpand(True)
            value = Gtk.Label(label="—", xalign=1)
            value.add_css_class("gpu-value")
            panel.attach(Gtk.Label(label=title, xalign=0), 0, 0, 1, 1)
            panel.attach(value, 1, 0, 1, 1)
            meter = Gtk.ProgressBar()
            meter.add_css_class("gpu-meter")
            meter.set_hexpand(True)
            panel.attach(meter, 0, 1, 2, 1)
            bmc_temperature_grid.attach(panel, column, 0, 1, 1)
            self.bmc_temperature_rows[key] = (value, meter)
        bmc_temperature_frame.set_child(bmc_temperature_grid)
        root.append(bmc_temperature_frame)

        connection = Gtk.Frame(label="BMC sensor connection")
        root.append(connection)
        form = Gtk.Grid(column_spacing=6, row_spacing=6)
        form.set_margin_top(6)
        form.set_margin_bottom(6)
        form.set_margin_start(12)
        form.set_margin_end(12)
        connection.set_child(form)

        form.attach(Gtk.Label(label="BMC address", xalign=0), 0, 0, 1, 1)
        self.host_entry = Gtk.Entry()
        self.host_entry.set_text(DEFAULT_BMC_HOST)
        self.host_entry.set_width_chars(14)
        form.attach(self.host_entry, 1, 0, 1, 1)

        form.attach(Gtk.Label(label="Account", xalign=0), 2, 0, 1, 1)
        self.user_entry = Gtk.Entry()
        self.user_entry.set_text(DEFAULT_BMC_USER)
        self.user_entry.set_width_chars(10)
        form.attach(self.user_entry, 3, 0, 1, 1)

        form.attach(Gtk.Label(label="Password", xalign=0), 4, 0, 1, 1)
        self.password_entry = Gtk.PasswordEntry()
        self.password_entry.set_width_chars(15)
        form.attach(self.password_entry, 5, 0, 1, 1)

        self.remember_password = Gtk.CheckButton(label="Save Password")
        self.remember_password.set_active(True)
        self.remember_password.set_sensitive(self.keyring_available)

        self.launch_at_startup = Gtk.CheckButton(label="Launch on Login")
        self.start_automatic_at_launch = Gtk.CheckButton(label="Start on Current Curve")
        self.start_automatic_at_launch.set_tooltip_text(
            "Opt in to the saved fan curve when the app launches. Quitting leaves manual control at the current speed."
        )
        controls = Gtk.Frame(label="Controls")
        controls_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        controls_grid.set_margin_top(6)
        controls_grid.set_margin_bottom(6)
        controls_grid.set_margin_start(12)
        controls_grid.set_margin_end(12)
        controls.set_child(controls_grid)
        root.append(controls)
        controls_grid.attach(self.remember_password, 0, 0, 1, 1)
        controls_grid.attach(self.launch_at_startup, 1, 0, 1, 1)
        controls_grid.attach(self.start_automatic_at_launch, 2, 0, 1, 1)

        button_contents = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.refresh_spinner = Gtk.Spinner()
        self.refresh_label = Gtk.Label(label="Refresh now")
        button_contents.append(self.refresh_spinner)
        button_contents.append(self.refresh_label)
        self.refresh_button = Gtk.Button()
        self.refresh_button.set_child(button_contents)
        self.refresh_button.connect("clicked", lambda _button: self.refresh(force_bmc=True))
        apply_curve = Gtk.Button(label="Apply Settings")
        apply_curve.connect("clicked", lambda _button: self._apply_fan_curve())
        self.start_control_button = Gtk.Button(label="Start Fan Control")
        self.start_control_button.connect("clicked", lambda _button: self._start_fan_control())
        self.stop_control_button = Gtk.Button(label="Stop (leave speed)")
        self.stop_control_button.set_sensitive(False)
        self.stop_control_button.connect("clicked", lambda _button: self._stop_fan_control())
        controls_grid.attach(apply_curve, 0, 1, 1, 1)
        controls_grid.attach(self.start_control_button, 1, 1, 1, 1)
        controls_grid.attach(self.stop_control_button, 2, 1, 1, 1)
        controls_grid.attach(self.refresh_button, 3, 1, 1, 1)

        curve_frame = Gtk.Frame(label="Fan curve settings")
        curve_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        curve_box.set_margin_top(6)
        curve_box.set_margin_bottom(6)
        curve_box.set_margin_start(12)
        curve_box.set_margin_end(12)
        curve_layout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.curve_grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        self.curve_grid.set_hexpand(True)
        self.curve_temperature_entries: list[Gtk.Entry] = []
        self.curve_rpm_entries: list[Gtk.Entry] = []
        for index in range(8):
            tier = Gtk.Grid(column_spacing=6, row_spacing=4)
            tier.attach(Gtk.Label(label=f"Tier {index + 1}", xalign=0), 0, 0, 2, 1)
            tier.attach(Gtk.Label(label="Temperature °C", xalign=0), 0, 1, 1, 1)
            tier.attach(Gtk.Label(label="Target RPM", xalign=0), 1, 1, 1, 1)
            temp_entry = Gtk.Entry()
            temp_entry.set_width_chars(4)
            rpm_entry = Gtk.Entry()
            rpm_entry.set_width_chars(4)
            self.curve_temperature_entries.append(temp_entry)
            self.curve_rpm_entries.append(rpm_entry)
            pair = Gtk.Grid(column_spacing=6)
            pair.set_margin_top(4)
            pair.set_margin_bottom(4)
            pair.set_margin_start(6)
            pair.set_margin_end(6)
            pair.attach(temp_entry, 0, 0, 1, 1)
            pair.attach(rpm_entry, 1, 0, 1, 1)
            pair_frame = Gtk.Frame()
            pair_frame.set_child(pair)
            tier.attach(pair_frame, 0, 2, 2, 1)
            # Place curve points 1,3,5,7 across the first row and 2,4,6,8 below.
            self.curve_grid.attach(tier, index // 2, index % 2, 1, 1)
        curve_layout.append(self.curve_grid)
        fan_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        fan_panel.add_css_class("fan-panel")
        fan_panel.set_valign(Gtk.Align.START)
        fan_title = Gtk.Label(label="Fan RPM", xalign=0)
        fan_title.add_css_class("sensor-header")
        fan_panel.append(fan_title)
        self.fan_grid = Gtk.Grid(column_spacing=6, row_spacing=4)
        fan_panel.append(self.fan_grid)
        curve_layout.append(fan_panel)
        curve_note = Gtk.Label(
            label="Start applies the selected curve; Stop/quit leaves manual control at the current speed.",
            xalign=0,
        )
        curve_note.add_css_class("muted")
        curve_box.append(curve_layout)
        curve_box.append(curve_note)
        curve_frame.set_child(curve_box)
        root.append(curve_frame)

        self.status = Gtk.Label(label="Waiting for first sample…", xalign=0)
        self.status.set_wrap(True)
        root.append(self.status)

        self._load_settings()
        for entry in [*self.curve_temperature_entries, *self.curve_rpm_entries]:
            entry.connect("changed", lambda _entry: self._queue_fan_curve_autosave())
        self.remember_password.connect("toggled", self._on_remember_password_toggled)
        self.launch_at_startup.connect("toggled", self._on_launch_at_startup_toggled)
        self.start_automatic_at_launch.connect("toggled", self._on_start_fan_control_toggled)
        self._install_css()
        self._sync_autostart()

    def _install_css(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .gpu-notice { background: #fff3cd; color: #684d03; padding: 8px; border-radius: 6px; }
            .gpu-title { font-weight: bold; font-size: 1.15em; }
            .gpu-value { font-size: 1.4em; font-weight: bold; }
            progressbar.gpu-meter trough { min-height: 12px; border-radius: 6px; background: #4a3434; }
            progressbar.gpu-meter progress { min-height: 12px; border-radius: 6px; background: #40c77a; }
            progressbar.gpu-meter.warning progress { background: #e4ad4a; }
            progressbar.gpu-meter.critical progress { background: #ec5f6d; }
            .fan-panel { background-color: #211819; border: 1px solid #493236; border-radius: 8px; padding: 8px; }
            .fan-panel label { color: #f2b5b7; }
            .fan-panel label.fan-rpm-green { color: #40c77a; font-weight: bold; }
            .fan-panel label.fan-rpm-amber { color: #e4ad4a; font-weight: bold; }
            .fan-panel label.fan-rpm-red { color: #ec5f6d; font-weight: bold; }
            .sensor-header { font-weight: bold; }
            .muted { color: #666; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _load_settings(self) -> None:
        parser = configparser.ConfigParser()
        try:
            parser.read(CONFIG_FILE)
            self.host_entry.set_text(parser.get("bmc", "host", fallback=DEFAULT_BMC_HOST))
            self.user_entry.set_text(parser.get("bmc", "username", fallback=DEFAULT_BMC_USER))
            self.remember_password.set_active(
                parser.getboolean("bmc", "remember_password", fallback=True)
            )
            self.launch_at_startup.set_active(
                parser.getboolean("app", "launch_at_startup", fallback=False)
            )
            self.start_automatic_at_launch.set_active(
                parser.getboolean("app", "start_automatic_at_launch", fallback=False)
            )
            temperatures = [
                parser.getint("fan_curve", f"temperature_{index + 1}", fallback=value)
                for index, value in enumerate(DEFAULT_FAN_TEMPERATURES)
            ]
            rpms = [
                parser.getint("fan_curve", f"rpm_{index + 1}", fallback=value)
                for index, value in enumerate(DEFAULT_FAN_RPMS)
            ]
            temperatures, rpms = validate_fan_curve(
                [str(value) for value in temperatures], [str(value) for value in rpms]
            )
            for temp_entry, value in zip(self.curve_temperature_entries, temperatures):
                temp_entry.set_text(str(value))
            for rpm_entry, value in zip(self.curve_rpm_entries, rpms):
                rpm_entry.set_text(str(value))
            if parser.has_section("window"):
                x = parser.getint("window", "x", fallback=-1)
                y = parser.getint("window", "y", fallback=-1)
                if x >= 0 and y >= 0:
                    self.saved_window_position = (x, y)
            if parser.has_option("bmc", "remember_password") and parser.has_option(
                "app", "launch_at_startup"
            ):
                self.saved_settings = self._current_settings()
        except (OSError, configparser.Error):
            pass
        except ValueError:
            for temp_entry, value in zip(
                self.curve_temperature_entries, DEFAULT_FAN_TEMPERATURES
            ):
                temp_entry.set_text(str(value))
            for rpm_entry, value in zip(self.curve_rpm_entries, DEFAULT_FAN_RPMS):
                rpm_entry.set_text(str(value))

    def _current_settings(self) -> tuple[str, str, bool, bool, bool]:
        return (
            self.host_entry.get_text().strip(),
            self.user_entry.get_text().strip(),
            self.remember_password.get_active(),
            self.launch_at_startup.get_active(),
            self.start_automatic_at_launch.get_active(),
        )

    def _read_config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.read(CONFIG_FILE)
        return parser

    def _write_config(self, parser: configparser.ConfigParser) -> None:
        CONFIG_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Write a complete replacement first, then atomically swap it into
        # place.  A close, crash, or power interruption cannot leave a
        # partially written settings file that falls back to default values.
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="settings-", suffix=".ini", dir=CONFIG_FILE.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as settings:
                parser.write(settings)
                settings.flush()
                os.fsync(settings.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, CONFIG_FILE)
            directory_descriptor = os.open(CONFIG_FILE.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def _restore_window_position(self) -> bool:
        if self.window_geometry is None:
            return GLib.SOURCE_REMOVE
        self.window_geometry.set_class_hint(
            self.window, "thermal-monitor", "ThermalMonitor"
        )
        if self.saved_window_position is not None:
            self.window_geometry.move(self.window, *self.saved_window_position)
        self.saved_window_position = self.window_geometry.position(self.window)
        return GLib.SOURCE_REMOVE

    def _poll_window_position(self) -> bool:
        if self.window_geometry is None:
            return GLib.SOURCE_REMOVE
        position = self.window_geometry.position(self.window)
        if position is None:
            return GLib.SOURCE_CONTINUE
        if position == self.saved_window_position:
            self.window_position_candidate = None
            return GLib.SOURCE_CONTINUE
        if position != self.window_position_candidate:
            self.window_position_candidate = position
            self.window_position_candidate_since = time.monotonic()
        elif time.monotonic() - self.window_position_candidate_since >= 1.5:
            self._save_window_position(position)
            self.window_position_candidate = None
        return GLib.SOURCE_CONTINUE

    def _save_window_position(self, position: tuple[int, int] | None = None) -> None:
        if self.window_geometry is None:
            return
        position = position or self.window_geometry.position(self.window)
        if position is None or position == self.saved_window_position:
            return
        parser = self._read_config()
        parser["window"] = {"x": str(position[0]), "y": str(position[1])}
        self._write_config(parser)
        self.saved_window_position = position

    def _on_window_close_request(self, _window: Gtk.Window) -> bool:
        # Preserve a complete, valid curve even if the user closes the window
        # without pressing the explicit Apply Settings button.
        self._apply_fan_curve(report_result=False)
        self._save_window_position()
        return False

    def _save_settings(self) -> None:
        current = self._current_settings()
        if current == self.saved_settings:
            return
        parser = self._read_config()
        parser["bmc"] = {
            "host": current[0],
            "username": current[1],
            "remember_password": str(current[2]).lower(),
        }
        parser["app"] = {
            "launch_at_startup": str(current[3]).lower(),
            "start_automatic_at_launch": str(current[4]).lower(),
        }
        self._write_config(parser)
        self.saved_settings = current

    def _queue_fan_curve_autosave(self) -> None:
        """Persist a valid edited curve shortly after typing stops."""
        if self.fan_curve_autosave_source is not None:
            GLib.source_remove(self.fan_curve_autosave_source)
        self.fan_curve_autosave_source = GLib.timeout_add(
            700, self._autosave_fan_curve
        )

    def _autosave_fan_curve(self) -> bool:
        self.fan_curve_autosave_source = None
        self._apply_fan_curve(report_result=False)
        return GLib.SOURCE_REMOVE

    def _apply_fan_curve(self, report_result: bool = True) -> bool:
        try:
            temperatures, rpms = validate_fan_curve(
                [entry.get_text() for entry in self.curve_temperature_entries],
                [entry.get_text() for entry in self.curve_rpm_entries],
            )
        except ValueError as exc:
            if report_result:
                self.status.set_text(f"Fan curve not saved: {exc}")
                self.status.add_css_class("error")
            return False

        self._save_settings()
        parser = self._read_config()
        parser["fan_curve"] = {}
        for index, (temperature, rpm) in enumerate(zip(temperatures, rpms), start=1):
            parser["fan_curve"][f"temperature_{index}"] = str(temperature)
            parser["fan_curve"][f"rpm_{index}"] = str(rpm)
        self._write_config(parser)
        self.fan_temperatures = temperatures
        self.fan_rpms = rpms
        self._sync_autostart(remove_when_disabled=True)
        if report_result:
            summary = ", ".join(
                f"{temperature}\N{DEGREE SIGN}C→{rpm} RPM"
                for temperature, rpm in zip(temperatures, rpms)
            )
            self.bmc_status = f"fan curve saved: {summary}"
            self._render_status()
        return True

    def _start_fan_control(self) -> None:
        if not self._apply_fan_curve():
            return
        if self.fan_control_active or self.fan_control_inflight:
            return
        temperatures = [gpu["temperature_c"] for gpu in self.latest_gpus]
        temperatures = [value for value in temperatures if value is not None]
        if not temperatures:
            self.bmc_status = "cannot start fan control until GPU temperatures are available"
            self._render_status()
            return
        if self.bmc_refresh_lock.locked():
            self.bmc_status = "BMC read in progress; wait for it to finish, then start fan control"
            self._render_status()
            return
        host = self.host_entry.get_text().strip()
        username = self.user_entry.get_text().strip()
        password = self.password_entry.get_text()
        if not password:
            self.bmc_status = "enter or restore the BMC password before starting fan control"
            self._render_status()
            return
        hottest = int(max(temperatures))
        self.target_fan_rpm = desired_fan_rpm(
            hottest, self.fan_temperatures, self.fan_rpms
        )
        duty = duty_for_rpm(self.target_fan_rpm)
        self.fan_control_inflight = True
        self.fan_control_lock.acquire()
        self.start_control_button.set_sensitive(False)
        self.stop_control_button.set_sensitive(False)
        self.bmc_status = f"enabling manual control and setting {self.target_fan_rpm} RPM target…"
        self._render_status()

        def worker() -> None:
            try:
                set_ipmi_fan_control(host, username, password)
                set_ipmi_fan_control(host, username, password, duty=duty)
                error = None
            except Exception as exc:
                error = str(exc)
            GLib.idle_add(self._apply_control_started, hottest, duty, error)

        self.executor.submit(worker)

    def _apply_control_started(self, hottest: int, duty: int, error: str | None) -> bool:
        self.fan_control_inflight = False
        self.fan_control_lock.release()
        if error:
            self.applied_fan_duty = None
            self.bmc_status = f"fan-control start failed: {error}"
            self.start_control_button.set_sensitive(True)
        else:
            self.fan_control_active = True
            self.applied_fan_duty = duty
            self.cool_samples = 0
            self.start_control_button.set_sensitive(False)
            self.stop_control_button.set_sensitive(True)
            self.bmc_status = (
                f"fan curve active: hottest GPU {hottest} °C, target "
                f"{self.target_fan_rpm} RPM ({duty}% duty); stop/quit leaves this speed manual"
            )
        self._render_status()
        return GLib.SOURCE_REMOVE

    def _stop_fan_control(self) -> None:
        self.fan_control_active = False
        self.stop_control_button.set_sensitive(False)
        self.start_control_button.set_sensitive(True)
        self.bmc_status = (
            f"curve stopped; manual control remains at the current speed "
            f"(last target {self.target_fan_rpm} RPM)"
        )
        self._render_status()

    def _update_fan_control(self, gpus: list[dict[str, Any]]) -> None:
        if not self.fan_control_active:
            return
        values = [gpu["temperature_c"] for gpu in gpus if gpu["temperature_c"] is not None]
        if not values:
            return
        hottest = int(max(values))
        desired = desired_fan_rpm(hottest, self.fan_temperatures, self.fan_rpms)
        if self.target_fan_rpm == 0 or desired > self.target_fan_rpm:
            self.target_fan_rpm = desired
            self.cool_samples = 0
        elif desired < self.target_fan_rpm:
            try:
                current_index = self.fan_rpms.index(self.target_fan_rpm)
            except ValueError:
                current_index = len(self.fan_rpms) - 1
            lower_index = max(0, current_index - 1)
            if hottest <= self.fan_temperatures[lower_index] - 4:
                self.cool_samples += 1
            else:
                self.cool_samples = 0
            if self.cool_samples >= 3:
                self.target_fan_rpm = desired
                self.cool_samples = 0
        if self.fan_control_inflight or self.fan_control_lock.locked():
            return
        duty = duty_for_rpm(self.target_fan_rpm)
        if duty == self.applied_fan_duty:
            return
        host = self.host_entry.get_text().strip()
        username = self.user_entry.get_text().strip()
        password = self.password_entry.get_text()
        if not password:
            self._stop_fan_control()
            self.bmc_status = "fan control stopped because the BMC password is unavailable; manual speed was left unchanged"
            self._render_status()
            return
        self.fan_control_inflight = True
        self.fan_control_lock.acquire()
        self.stop_control_button.set_sensitive(False)

        def worker() -> None:
            try:
                set_ipmi_fan_control(host, username, password, duty=duty)
                error = None
            except Exception as exc:
                error = str(exc)
            GLib.idle_add(self._apply_control_update, hottest, duty, error)

        self.executor.submit(worker)

    def _apply_control_update(self, hottest: int, duty: int, error: str | None) -> bool:
        self.fan_control_inflight = False
        self.fan_control_lock.release()
        if error:
            self.fan_control_active = False
            self.stop_control_button.set_sensitive(False)
            self.start_control_button.set_sensitive(True)
            self.bmc_status = f"fan-control update failed; manual mode may remain active: {error}"
        else:
            self.applied_fan_duty = duty
            self.bmc_status = (
                f"fan curve active: hottest GPU {hottest} °C, target "
                f"{self.target_fan_rpm} RPM ({duty}% duty); stop/quit leaves this speed manual"
            )
            self.stop_control_button.set_sensitive(self.fan_control_active)
        self._render_status()
        return GLib.SOURCE_REMOVE

    def _maybe_start_fan_control(self) -> None:
        has_gpu_temperature = any(
            gpu["temperature_c"] is not None for gpu in self.latest_gpus
        )
        if fan_control_autostart_ready(
            self.start_automatic_at_launch.get_active(),
            self.fan_control_autostart_attempted,
            has_gpu_temperature,
            bool(self.password_entry.get_text()),
            self.bmc_refresh_lock.locked(),
        ):
            self.fan_control_autostart_attempted = True
            self._start_fan_control()

    def _on_launch_at_startup_toggled(self, _button: Gtk.CheckButton) -> None:
        self._save_settings()
        try:
            self._sync_autostart(remove_when_disabled=True)
        except OSError as exc:
            self.status.set_text(f"Could not update desktop startup setting: {exc}")
            self.status.add_css_class("error")

    def _on_start_fan_control_toggled(self, _button: Gtk.CheckButton) -> None:
        self._save_settings()
        # This checkbox controls the next launch only, never an unexpected
        # mid-session transition into manual fan mode.
        self.fan_control_autostart_attempted = True

    def _sync_autostart(self, remove_when_disabled: bool = False) -> None:
        if self.launch_at_startup.get_active():
            AUTOSTART_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            entry = (
                "[Desktop Entry]\nType=Application\nName=Thermal Monitor\n"
                "Comment=GPU and BMC thermal monitoring\n"
                f"Exec={sys.executable} {APP_SCRIPT}\n"
                "Terminal=false\nX-GNOME-Autostart-enabled=true\n"
            )
            AUTOSTART_FILE.write_text(entry, encoding="utf-8")
            os.chmod(AUTOSTART_FILE, 0o600)
        elif remove_when_disabled and AUTOSTART_FILE.exists():
            AUTOSTART_FILE.unlink()

    def _restore_saved_password(self) -> None:
        if not self.keyring_available or not self.remember_password.get_active():
            return
        host = self.host_entry.get_text().strip()
        username = self.user_entry.get_text().strip()
        future = self.executor.submit(credential_store.get_password, host, username)

        def complete(task: Any) -> None:
            try:
                password = task.result()
                error = None
            except Exception as exc:
                password, error = None, str(exc)
            GLib.idle_add(self._apply_restored_password, host, username, password, error)

        future.add_done_callback(complete)

    def _apply_restored_password(
        self, host: str, username: str, password: str | None, error: str | None
    ) -> bool:
        if (host, username) != (
            self.host_entry.get_text().strip(), self.user_entry.get_text().strip()
        ):
            return GLib.SOURCE_REMOVE
        if error:
            self.status.set_text(f"Secure keyring lookup failed: {error}")
        elif password:
            self.password_entry.set_text(password)
            self._maybe_start_fan_control()
            self.refresh(force_bmc=True)
        return GLib.SOURCE_REMOVE

    def _on_remember_password_toggled(self, _button: Gtk.CheckButton) -> None:
        self._save_settings()
        if self.remember_password.get_active() or not self.keyring_available:
            return
        host = self.host_entry.get_text().strip()
        username = self.user_entry.get_text().strip()

        def clear_saved_password() -> None:
            try:
                credential_store.clear_password(host, username)
            except Exception as exc:
                GLib.idle_add(self.status.set_text, f"Could not clear saved password: {exc}")

        self.executor.submit(clear_saved_password)

    def _poll(self) -> bool:
        self.refresh()
        return True

    def refresh(self, force_bmc: bool = False) -> None:
        self._refresh_gpus()
        self._refresh_bmc(force=force_bmc)

    def _refresh_gpus(self) -> None:
        if not self.gpu_refresh_lock.acquire(blocking=False):
            return

        def worker() -> None:
            try:
                gpus, error = query_gpus(), None
            except Exception as exc:  # Render hardware/tool errors in the UI.
                gpus, error = None, str(exc)
            GLib.idle_add(self._apply_gpu_result, gpus, error)

        self.executor.submit(worker)

    def _apply_gpu_result(self, gpus: list[dict[str, Any]] | None, error: str | None) -> bool:
        try:
            self.gpu_error = error
            if gpus is not None:
                self.latest_gpus = gpus
                self._render_gpus(gpus)
                self.gpu_status = f"updated at {time.strftime('%H:%M:%S')}"
                self._update_fan_control(gpus)
                self._maybe_start_fan_control()
            else:
                self.gpu_status = f"error: {error}"
            self._render_status()
        finally:
            self.gpu_refresh_lock.release()
        return GLib.SOURCE_REMOVE

    def _refresh_bmc(self, force: bool = False) -> None:
        self._save_settings()
        self.password = self.password_entry.get_text()
        host = self.host_entry.get_text().strip()
        username = self.user_entry.get_text().strip()
        if not bmc_poll_due(bool(self.password), force, time.monotonic(), self.next_bmc_attempt):
            if not self.password:
                self.bmc_status = (
                    "enter the password, then press Refresh now"
                    if self.keyring_available else
                    "enter the password; secure keyring storage is unavailable"
                )
            self._render_status()
            return
        if not self.bmc_refresh_lock.acquire(blocking=False):
            if force:
                self.bmc_status = "a BMC request is already in progress; please wait"
                self._render_status()
            return

        password = self.password
        remember_password = self.remember_password.get_active()
        self.refresh_button.set_sensitive(False)
        self.remember_password.set_sensitive(False)
        self.refresh_spinner.start()
        self.refresh_label.set_text("Contacting BMC…")
        self.bmc_status = "contacting BMC…"
        self._render_status()

        def worker() -> None:
            try:
                with self.fan_control_lock:
                    sensors = query_ipmi(host, username, password)
                error = None
                credential_warning = None
                if remember_password and self.keyring_available:
                    try:
                        if not credential_store.store_password(host, username, password):
                            raise RuntimeError("the keyring rejected the saved password")
                    except Exception as exc:
                        credential_warning = f"Password was not saved to the desktop keyring: {exc}"
                self.next_bmc_attempt = time.monotonic() + BMC_POLL_SECONDS
                self.bmc_backoff_seconds = 60
                self.bmc_failed = False
                self.bmc_status = f"sensors updated; automatic check in {BMC_POLL_SECONDS}s"
            except Exception as exc:
                sensors, error, credential_warning = None, str(exc), None
                is_session_exhaustion = "insufficient resources for session" in error.lower()
                delay = max(self.bmc_backoff_seconds, 60 if is_session_exhaustion else BMC_POLL_SECONDS)
                self.next_bmc_attempt = time.monotonic() + delay
                self.bmc_backoff_seconds = min(delay * 2, BMC_MAX_BACKOFF_SECONDS)
                self.bmc_status = f"{error} (automatic retry in {delay}s)"
                self.bmc_failed = True
            GLib.idle_add(self._apply_bmc_result, sensors, error, credential_warning)

        self.executor.submit(worker)

    def _apply_bmc_result(
        self,
        sensors: list[dict[str, str]] | None,
        error: str | None,
        credential_warning: str | None,
    ) -> bool:
        try:
            if sensors is not None:
                self._render_sensors(sensors)
            if credential_warning:
                self.bmc_status = f"{self.bmc_status} · {credential_warning}"
            self.bmc_failed = error is not None
            self._render_status()
        finally:
            self.refresh_spinner.stop()
            self.refresh_label.set_text("Refresh now")
            self.refresh_button.set_sensitive(True)
            self.remember_password.set_sensitive(self.keyring_available)
            self.bmc_refresh_lock.release()
        return GLib.SOURCE_REMOVE

    def _render_status(self) -> None:
        bmc_status = self.bmc_status
        if self.next_bmc_attempt > time.monotonic() and "contacting BMC" not in bmc_status:
            remaining = max(1, int(self.next_bmc_attempt - time.monotonic()))
            if "automatic check in" not in bmc_status and "automatic retry in" not in bmc_status:
                bmc_status = f"{bmc_status} · next automatic check in {remaining}s"
        self.status.set_text(f"GPU: {self.gpu_status}  ·  BMC: {bmc_status}")
        if self.gpu_error or self.bmc_failed:
            self.status.add_css_class("error")
        else:
            self.status.remove_css_class("error")

    def _render_gpus(self, gpus: list[dict[str, Any]]) -> None:
        while child := self.gpu_grid.get_first_child():
            self.gpu_grid.remove(child)
        self.gpu_rows.clear()
        for column, gpu in enumerate(gpus):
            panel = Gtk.Grid(column_spacing=9, row_spacing=5)
            panel.set_hexpand(True)
            title = Gtk.Label(label=f"GPU {gpu['index']} · {gpu['name']}", xalign=0)
            title.add_css_class("gpu-title")
            panel.attach(title, 0, 0, 3, 1)

            values = (
                (
                    "Temperature", display_number(gpu["temperature_c"], " °C"),
                    gpu["temperature_c"], 100,
                    GPU_TEMPERATURE_WARNING_C / 100, GPU_TEMPERATURE_CRITICAL_C / 100,
                ),
                (
                    "Power", f"{display_number(gpu['power_w'], ' W')} / {display_number(gpu['power_limit_w'], ' W')}",
                    gpu["power_w"], gpu["power_limit_w"],
                    GPU_POWER_WARNING_FRACTION, 1.0,
                ),
                (
                    "Utilization", display_number(gpu["utilization_pct"], "%"),
                    gpu["utilization_pct"], 100,
                    GPU_UTILIZATION_WARNING_PERCENT / 100, None,
                ),
                (
                    "Memory", f"{display_number(gpu['memory_used_mib'], ' MiB')} / {display_number(gpu['memory_total_mib'], ' MiB')}",
                    gpu["memory_used_mib"], gpu["memory_total_mib"],
                    GPU_MEMORY_WARNING_FRACTION, 1.0,
                ),
            )
            widgets: dict[str, dict[str, Gtk.Widget]] = {}
            for row, (name, value, current, maximum, warning, critical) in enumerate(values, start=1):
                panel.attach(Gtk.Label(label=name, xalign=0), 0, row, 1, 1)
                value_label = Gtk.Label(label=value, xalign=1)
                if name == "Temperature":
                    value_label.add_css_class("gpu-value")
                panel.attach(value_label, 1, row, 1, 1)
                meter = Gtk.ProgressBar()
                meter.add_css_class("gpu-meter")
                meter.set_hexpand(True)
                meter.set_valign(Gtk.Align.CENTER)
                meter.set_show_text(False)
                panel.attach(meter, 2, row, 1, 1)
                widgets[name] = {"value": value_label, "meter": meter}
                self._update_gpu_meter(meter, current, maximum, warning, critical)
            self.gpu_rows.append(widgets)
            self.gpu_grid.attach(panel, column % 2, column // 2, 1, 1)

    @staticmethod
    def _update_gpu_meter(
        meter: Gtk.ProgressBar,
        current: float | None,
        maximum: float | None,
        warning: float | None,
        critical: float | None,
    ) -> None:
        fraction, severity = gpu_bar_state(current, maximum, warning, critical)
        meter.set_fraction(fraction)
        if severity != "normal":
            meter.add_css_class(severity)

    def _fan_rpm_color_class(self, rpm: float | None) -> str | None:
        """Classify a live fan reading by the saved eight-tier curve."""
        if rpm is None or len(self.fan_rpms) < 8:
            return None
        if rpm <= self.fan_rpms[3]:
            return "fan-rpm-green"
        if rpm <= self.fan_rpms[5]:
            return "fan-rpm-amber"
        return "fan-rpm-red"

    def _render_sensors(self, sensors: list[dict[str, str]]) -> None:
        while child := self.fan_grid.get_first_child():
            self.fan_grid.remove(child)
        for name, (value_label, meter) in self.bmc_temperature_rows.items():
            sensor = next((item for item in sensors if item["name"].casefold() == name.casefold()), None)
            temperature = parse_temperature_c(sensor["reading"]) if sensor else None
            value_label.set_text(display_number(temperature, " °C"))
            fraction, _severity = gpu_bar_state(
                temperature, BMC_TEMPERATURE_BAR_MAX_C, None, None
            )
            meter.set_fraction(fraction)
            meter.remove_css_class("warning")
            meter.remove_css_class("critical")
            if sensor and sensor["status"].casefold() not in {"ok", "normal"}:
                meter.add_css_class("critical")
        fan_sensors = [
            sensor for sensor in sensors
            if fan_sensor_grid_position(sensor["name"]) is not None
        ]
        if not fan_sensors:
            self.fan_grid.attach(Gtk.Label(label="No fan RPM readings."), 0, 0, 1, 1)
            return

        def attach_sensor(sensor: dict[str, str], row: int) -> None:
            name = Gtk.Label(label=sensor["name"], xalign=0)
            name.set_size_request(54, -1)
            value = Gtk.Label(label=sensor["reading"], xalign=1)
            value.set_size_request(76, -1)
            rpm_color = self._fan_rpm_color_class(
                parse_temperature_c(sensor["reading"])
            )
            if rpm_color:
                value.add_css_class(rpm_color)
            state = Gtk.Label(label=sensor["status"], xalign=1)
            state.add_css_class("muted")
            state.set_size_request(28, -1)
            # Keep the status with its preceding Fan/RPM pair rather than
            # visually attaching it to the following fan group.
            state.set_margin_end(14)
            self.fan_grid.attach(name, 0, row, 1, 1)
            self.fan_grid.attach(value, 1, row, 1, 1)
            self.fan_grid.attach(state, 2, row, 1, 1)

        for sensor in fan_sensors:
            position = fan_sensor_grid_position(sensor["name"])
            column, pair_row = position
            attach_sensor(sensor, column * 2 + pair_row)

    def do_shutdown(self) -> None:
        # Deliberately send no mode-reset or duty command: the BMC stays in
        # manual mode at the last applied speed when this app exits.
        self._save_window_position()
        self.fan_control_active = False
        self.password = ""
        self.executor.shutdown(wait=False, cancel_futures=True)
        Gtk.Application.do_shutdown(self)


if __name__ == "__main__":
    raise SystemExit(ThermalMonitor().run(None))
