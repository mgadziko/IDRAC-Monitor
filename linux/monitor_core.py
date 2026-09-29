"""Read-only GPU and BMC telemetry collection for the Linux Thermal Monitor."""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
from typing import Any


GPU_QUERY = (
    "index,name,temperature.gpu,power.draw,power.limit,utilization.gpu,"
    "memory.used,memory.total"
)
DEFAULT_FAN_TEMPERATURES = (0, 60, 65, 70, 75, 80, 85, 90)
DEFAULT_FAN_RPMS = (3000, 4000, 5000, 6000, 7000, 8000, 8700, 9360)
RPM_CALIBRATION = (2800, 4400, 6400, 7200, 8700, 9360)
DUTY_CALIBRATION = (7, 16, 30, 35, 50, 53)


def _number(value: str) -> float | None:
    value = value.strip()
    if not value or value.upper() in {"N/A", "[N/A]", "NOT SUPPORTED"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_temperature_c(reading: str) -> float | None:
    """Extract the leading numeric Celsius value from an IPMI sensor reading."""
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", reading.strip())
    return float(match.group(0)) if match else None


def fan_sensor_grid_position(name: str) -> tuple[int, int] | None:
    """Return (column, row) for fans 1,3,5 above 2,4,6."""
    match = re.match(r"fan\s*(\d+)", name.strip(), re.IGNORECASE)
    if not match:
        return None
    fan_number = int(match.group(1))
    if not 1 <= fan_number <= 6:
        return None
    return (fan_number - 1) // 2, (fan_number - 1) % 2


def gpu_bar_state(
    current: float | None,
    maximum: float | None,
    warning_fraction: float | None,
    critical_fraction: float | None,
) -> tuple[float, str]:
    """Return a clamped progress fraction and nvgraph-style severity class."""
    if current is None or maximum is None or maximum <= 0:
        return 0.0, "normal"
    ratio = max(0.0, current / maximum)
    if critical_fraction is not None and ratio > critical_fraction:
        severity = "critical"
    elif warning_fraction is not None and ratio >= warning_fraction:
        severity = "warning"
    else:
        severity = "normal"
    return min(ratio, 1.0), severity


def bmc_poll_due(
    has_password: bool, force: bool, now: float, next_attempt: float
) -> bool:
    """Manual refresh bypasses the automatic BMC retry/poll interval."""
    return has_password and (force or now >= next_attempt)


def validate_fan_curve(temperatures: list[str], rpms: list[str]) -> tuple[list[int], list[int]]:
    """Validate the eight-point Linux curve and supported RPM range."""
    if len(temperatures) != 8 or len(rpms) != 8:
        raise ValueError("Enter all eight temperature and target RPM pairs.")
    try:
        parsed_temperatures = [int(value.strip()) for value in temperatures]
        parsed_rpms = [int(value.strip()) for value in rpms]
    except ValueError as exc:
        raise ValueError("Temperature thresholds and RPM targets must be whole numbers.") from exc
    if any(right <= left for left, right in zip(parsed_temperatures, parsed_temperatures[1:])):
        raise ValueError("Temperature thresholds must be strictly increasing.")
    if any(rpm < 2800 or rpm > 9360 for rpm in parsed_rpms):
        raise ValueError("Target RPM must be between 2800 and 9360.")
    return parsed_temperatures, parsed_rpms


def duty_for_rpm(rpm: int) -> int:
    """Map RPM to the R730 duty table used by the existing Windows app."""
    if rpm <= RPM_CALIBRATION[0]:
        return DUTY_CALIBRATION[0]
    for index in range(1, len(RPM_CALIBRATION)):
        if rpm <= RPM_CALIBRATION[index]:
            fraction = (rpm - RPM_CALIBRATION[index - 1]) / (
                RPM_CALIBRATION[index] - RPM_CALIBRATION[index - 1]
            )
            return round(
                DUTY_CALIBRATION[index - 1]
                + fraction * (DUTY_CALIBRATION[index] - DUTY_CALIBRATION[index - 1])
            )
    return DUTY_CALIBRATION[-1]


def desired_fan_rpm(temperature_c: int, temperatures: list[int], rpms: list[int]) -> int:
    """Select the highest target tier whose temperature threshold is met."""
    target = rpms[0]
    for threshold, rpm in zip(temperatures[1:], rpms[1:]):
        if temperature_c >= threshold:
            target = rpm
    return target


def fan_control_autostart_ready(
    enabled: bool,
    attempted: bool,
    has_gpu_temperature: bool,
    has_password: bool,
    bmc_read_in_progress: bool,
) -> bool:
    """Do not consume the one-time auto-start attempt while a BMC read blocks it."""
    return (
        enabled
        and not attempted
        and has_gpu_temperature
        and has_password
        and not bmc_read_in_progress
    )


def parse_gpu_output(output: str) -> list[dict[str, Any]]:
    """Parse nvidia-smi's CSV output without depending on localized text."""
    result: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(output), skipinitialspace=True):
        if len(row) < 8:
            continue
        result.append(
            {
                "index": int(row[0].strip()),
                "name": row[1].strip(),
                "temperature_c": _number(row[2]),
                "power_w": _number(row[3]),
                "power_limit_w": _number(row[4]),
                "utilization_pct": _number(row[5]),
                "memory_used_mib": _number(row[6]),
                "memory_total_mib": _number(row[7]),
            }
        )
    return result


def query_gpus() -> list[dict[str, Any]]:
    binary = shutil.which("nvidia-smi")
    if not binary:
        raise RuntimeError("nvidia-smi was not found; install/repair the NVIDIA driver tools.")
    proc = subprocess.run(
        [binary, f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits"],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
    )
    if proc.returncode:
        message = proc.stderr.strip() or "nvidia-smi returned an error."
        raise RuntimeError(message[-300:])
    gpus = parse_gpu_output(proc.stdout)
    if not gpus:
        raise RuntimeError("nvidia-smi returned no GPU telemetry.")
    return gpus


def parse_ipmi_sensors(output: str) -> list[dict[str, str]]:
    """Extract only fan and thermal sensors from ipmitool SDR/SDR-ELIST output."""
    sensors: list[dict[str, str]] = []
    wanted = re.compile(r"fan|temp|thermal|inlet|exhaust|ambient", re.IGNORECASE)
    for line in output.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 3 or not wanted.search(fields[0]):
            continue
        # `sdr elist full` includes both the raw sensor byte (for example
        # `30h`) and the SDR-converted value (for example `4200 RPM`) in its
        # final column. Prefer that engineering value when present; older or
        # abbreviated output formats still use the second column.
        reading = fields[-1] if len(fields) >= 5 else fields[1]
        sensors.append(
            {"name": fields[0], "reading": reading, "status": fields[2]}
        )
    return sensors


def query_ipmi(host: str, username: str, password: str) -> list[dict[str, str]]:
    """Read remote BMC sensors. The password is sent only in the child environment."""
    if not host.strip() or not username.strip():
        raise RuntimeError("Enter the BMC address and account name.")
    if not password:
        raise RuntimeError("Enter the BMC password to read chassis sensors.")
    binary = shutil.which("ipmitool")
    if not binary:
        raise RuntimeError("ipmitool was not found; install it with the Linux package manager.")

    env = os.environ.copy()
    env["IPMI_PASSWORD"] = password
    proc = subprocess.run(
        [
            binary,
            "-I", "lanplus",
            "-H", host.strip(),
            "-U", username.strip(),
            "-E",
            "sdr", "elist", "full",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=12,
        env=env,
    )
    if proc.returncode:
        # Never echo child environment or credentials in an error message.
        message = proc.stderr.strip() or "BMC sensor query failed. Check address and credentials."
        raise RuntimeError(message[-300:])
    sensors = parse_ipmi_sensors(proc.stdout)
    if not sensors:
        raise RuntimeError("The BMC responded, but no fan or thermal sensors were returned.")
    return sensors


def set_ipmi_fan_control(host: str, username: str, password: str, *, duty: int | None = None) -> None:
    """Enable manual fan mode, or set all zones to a validated duty percentage."""
    if not host.strip() or not username.strip() or not password:
        raise RuntimeError("Enter the BMC address, account name, and password first.")
    if duty is not None and not 7 <= duty <= 53:
        raise ValueError("Fan duty must be between 7 and 53 percent.")
    binary = shutil.which("ipmitool")
    if not binary:
        raise RuntimeError("ipmitool was not found; install it with the Linux package manager.")
    env = os.environ.copy()
    env["IPMI_PASSWORD"] = password
    raw = ["0x30", "0x30", "0x01", "0x00"] if duty is None else [
        "0x30", "0x30", "0x02", "0xff", f"0x{duty:02x}"
    ]
    proc = subprocess.run(
        [binary, "-I", "lanplus", "-H", host.strip(), "-U", username.strip(), "-E", "raw", *raw],
        check=False,
        capture_output=True,
        text=True,
        timeout=12,
        env=env,
    )
    if proc.returncode:
        message = proc.stderr.strip() or "The BMC rejected the fan-control command."
        raise RuntimeError(message[-300:])
