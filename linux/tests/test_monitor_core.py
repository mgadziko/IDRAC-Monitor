import unittest
from types import SimpleNamespace
from unittest.mock import patch

import monitor_core

from monitor_core import (
    DEFAULT_FAN_RPMS,
    DEFAULT_FAN_TEMPERATURES,
    bmc_poll_due,
    desired_fan_rpm,
    duty_for_rpm,
    fan_control_autostart_ready,
    fan_sensor_grid_position,
    gpu_bar_state,
    normalize_sensor_name,
    parse_gpu_output,
    parse_ipmi_sensors,
    temperature_sensor_key,
    parse_temperature_c,
    restore_ipmi_automatic_control,
    validate_fan_curve,
)


class TelemetryParserTests(unittest.TestCase):
    def test_gpu_csv_parsing(self):
        rows = parse_gpu_output(
            "0, Tesla P40, 30, 9.06, 250.00, 0, 100, 24576\n"
            "1, Tesla P40, 39, 9.93, 250.00, 1, 200, 24576\n"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "Tesla P40")
        self.assertEqual(rows[1]["temperature_c"], 39.0)
        self.assertEqual(rows[0]["memory_total_mib"], 24576.0)

    def test_gpu_unavailable_fields_are_none(self):
        rows = parse_gpu_output("0, Tesla P40, 30, N/A, 250, N/A, 0, 24576\n")
        self.assertIsNone(rows[0]["power_w"])
        self.assertIsNone(rows[0]["utilization_pct"])

    def test_ipmi_filters_fan_and_thermal_sensors(self):
        rows = parse_ipmi_sensors(
            "Fan1 | 30h | ok | 7.1 | 4200 RPM\n"
            "Inlet Temp | 04h | ok | 0.0 | 24 degrees C\n"
            "Power Supply 1 | 210 Watts | ok\n"
        )
        self.assertEqual([row["name"] for row in rows], ["Fan1", "Inlet Temp"])
        self.assertEqual(rows[0]["reading"], "4200 RPM")
        self.assertEqual(rows[1]["reading"], "24 degrees C")

    def test_ipmi_abbreviated_output_keeps_reading_column(self):
        rows = parse_ipmi_sensors("Fan1A | 4200 RPM | ok\nAmbient Temp | 24 degrees C | ok\n")
        self.assertEqual(rows[0]["reading"], "4200 RPM")
        self.assertEqual(rows[1]["reading"], "24 degrees C")

    def test_gpu_bar_state_uses_warning_and_critical_thresholds(self):
        self.assertEqual(gpu_bar_state(75, 100, 0.75, 0.80), (0.75, "warning"))
        self.assertEqual(gpu_bar_state(80, 100, 0.75, 0.80), (0.8, "warning"))
        self.assertEqual(gpu_bar_state(81, 100, 0.75, 0.80), (0.81, "critical"))
        self.assertEqual(gpu_bar_state(110, 100, 0.90, 1.0), (1.0, "critical"))
        self.assertEqual(gpu_bar_state(None, 100, 0.90, 1.0), (0.0, "normal"))

    def test_ipmi_temperature_value_parsing(self):
        self.assertEqual(parse_temperature_c("24 degrees C"), 24.0)
        self.assertEqual(parse_temperature_c("37.5 °C"), 37.5)
        self.assertIsNone(parse_temperature_c("No reading"))

    def test_ipmi_cpu_temperature_sensor_labels_are_preserved_and_normalized(self):
        rows = parse_ipmi_sensors(
            "CPU1 Temp | 0Eh | ok | 3.1 | 52 degrees C\n"
            "CPU 2 Temp | 0Fh | ok | 3.2 | 55 degrees C\n"
        )
        self.assertEqual([row["name"] for row in rows], ["CPU1 Temp", "CPU 2 Temp"])
        self.assertEqual(parse_temperature_c(rows[0]["reading"]), 52.0)
        self.assertEqual(normalize_sensor_name(rows[0]["name"]), normalize_sensor_name("CPU1 Temp"))
        self.assertEqual(normalize_sensor_name(rows[1]["name"]), normalize_sensor_name("CPU2 Temp"))

    def test_r730_generic_temperature_sdr_ids_map_to_cpu_sockets(self):
        rows = parse_ipmi_sensors(
            "Temp | 0Eh | ok | 3.1 | 48 degrees C\n"
            "Temp | 0Fh | ok | 3.2 | 54 degrees C\n"
        )
        self.assertEqual([temperature_sensor_key(row) for row in rows], ["cpu1temp", "cpu2temp"])

    def test_fan_sensor_order_is_odd_then_even_by_row(self):
        self.assertEqual(fan_sensor_grid_position("Fan1"), (0, 0))
        self.assertEqual(fan_sensor_grid_position("Fan 2"), (0, 1))
        self.assertEqual(fan_sensor_grid_position("Fan3"), (1, 0))
        self.assertEqual(fan_sensor_grid_position("Fan4"), (1, 1))
        self.assertEqual(fan_sensor_grid_position("Fan5"), (2, 0))
        self.assertEqual(fan_sensor_grid_position("Fan6"), (2, 1))
        self.assertIsNone(fan_sensor_grid_position("Inlet Temp"))

    def test_manual_bmc_refresh_bypasses_automatic_backoff(self):
        self.assertFalse(bmc_poll_due(True, False, 10, 70))
        self.assertTrue(bmc_poll_due(True, True, 10, 70))
        self.assertFalse(bmc_poll_due(False, True, 10, 70))

    def test_restore_uses_dell_automatic_control_command(self):
        with patch.object(monitor_core.shutil, "which", return_value="/usr/bin/ipmitool"), patch.object(
            monitor_core.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="")
        ) as run:
            restore_ipmi_automatic_control("192.168.4.122", "root", "not-a-real-password")
        args = run.call_args.args[0]
        self.assertEqual(args[-5:], ["raw", "0x30", "0x30", "0x01", "0x01"])
        self.assertEqual(run.call_args.kwargs["env"]["IPMI_PASSWORD"], "not-a-real-password")

    def test_fan_curve_defaults_match_windows_app(self):
        temperatures, rpms = validate_fan_curve(
            [str(value) for value in DEFAULT_FAN_TEMPERATURES],
            [str(value) for value in DEFAULT_FAN_RPMS],
        )
        self.assertEqual(temperatures, [0, 60, 65, 70, 75, 80, 85, 90])
        self.assertEqual(rpms, [3000, 4000, 5000, 6000, 7000, 8000, 8700, 9360])

    def test_fan_curve_rejects_invalid_order_and_rpm(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            validate_fan_curve(["0", "60", "65", "70", "75", "80", "85", "84"], ["3000"] * 8)
        with self.assertRaisesRegex(ValueError, "between 2800 and 9360"):
            validate_fan_curve([str(v) for v in DEFAULT_FAN_TEMPERATURES], ["2000"] * 8)

    def test_curve_target_uses_highest_threshold_met(self):
        self.assertEqual(desired_fan_rpm(59, list(DEFAULT_FAN_TEMPERATURES), list(DEFAULT_FAN_RPMS)), 3000)
        self.assertEqual(desired_fan_rpm(72, list(DEFAULT_FAN_TEMPERATURES), list(DEFAULT_FAN_RPMS)), 6000)
        self.assertEqual(desired_fan_rpm(84, list(DEFAULT_FAN_TEMPERATURES), list(DEFAULT_FAN_RPMS)), 8000)
        self.assertEqual(desired_fan_rpm(87, list(DEFAULT_FAN_TEMPERATURES), list(DEFAULT_FAN_RPMS)), 8700)
        self.assertEqual(desired_fan_rpm(90, list(DEFAULT_FAN_TEMPERATURES), list(DEFAULT_FAN_RPMS)), 9360)

    def test_duty_calibration_matches_windows_app(self):
        self.assertEqual(duty_for_rpm(2800), 7)
        self.assertEqual(duty_for_rpm(4400), 16)
        self.assertEqual(duty_for_rpm(6400), 30)
        self.assertEqual(duty_for_rpm(9360), 53)
        self.assertEqual(duty_for_rpm(7200), 35)

    def test_autostart_retries_after_initial_bmc_read_finishes(self):
        self.assertFalse(fan_control_autostart_ready(True, False, True, True, True))
        self.assertTrue(fan_control_autostart_ready(True, False, True, True, False))
        self.assertFalse(fan_control_autostart_ready(True, True, True, True, False))


if __name__ == "__main__":
    unittest.main()
