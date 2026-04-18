"""
ADB-based CMS control for Hisense U8G.

Calls IHisVideoApi directly via app_process running in u:r:shell:s0
SELinux context, which has the necessary hwservice_manager permissions.
This bypasses the untrusted_app SELinux restriction that blocks the
CmsController APK approach — no root or Magisk module required.

The cms_tool.dex file (from hisense-cms-controller/tool/) must be pushed
to /data/local/tmp/cms_tool.dex on the TV once.  After that, all set/get
operations work over plain ADB without any special TV-side configuration.

Channel and range constants match HisVideoApi.java from TvSettingsPlus.apk:
  COLOR_RED=1, COLOR_GREEN=2, COLOR_BLUE=3,
  COLOR_YELLOW=4, COLOR_CYAN=5, COLOR_MAGENTA=6
  Hue/Saturation/Brightness range: -10 to +10
"""

import subprocess
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

# Path to the pre-built DEX on the TV.  Push once with push_cms_tool().
CMS_TOOL_DEVICE_PATH = "/data/local/tmp/cms_tool.dex"

# TvSettingsPlus.apk provides the real IHisVideoApi implementation at runtime.
TVSETTINGS_APK = "/system/priv-app/TvSettingsPlus/TvSettingsPlus.apk"

# CLASSPATH used for every app_process invocation.
_CLASSPATH = f"{CMS_TOOL_DEVICE_PATH}:{TVSETTINGS_APK}"

# Path to the local cms_tool.dex (relative to this file → repo root → tool/).
_LOCAL_DEX = Path(__file__).parent.parent.parent / "hisense-cms-controller" / "tool" / "cms_tool.dex"

# Maps colour names (matching TVProfile.CMS_COLOURS) to HisVideoApi channel IDs.
CMS_CHANNELS: dict[str, int] = {
    "Red":     1,
    "Green":   2,
    "Blue":    3,
    "Yellow":  4,
    "Cyan":    5,
    "Magenta": 6,
}

# Maps TVProfile.CMS_CONTROLS labels to CmsTool action strings.
CMS_SET_ACTIONS: dict[str, str] = {
    "Hue":        "set_hue",
    "Saturation": "set_saturation",
    "Brightness": "set_brightness",
}

CMS_GET_ACTIONS: dict[str, str] = {
    "Hue":        "get_hue",
    "Saturation": "get_saturation",
    "Brightness": "get_brightness",
}

CMS_MIN = -10
CMS_MAX = 10

# Maps main picture control names to CmsTool action strings.
PICTURE_SET_ACTIONS: dict[str, str] = {
    "PictureMode": "set_picture_mode",
    "Brightness":  "set_brightness_main",
    "Contrast":    "set_contrast",
    "Saturation":  "set_saturation_main",
}

PICTURE_GET_ACTIONS: dict[str, str] = {
    "PictureMode": "get_picture_mode",
    "Brightness":  "get_brightness_main",
    "Contrast":    "get_contrast",
    "Saturation":  "get_saturation_main",
}

PICTURE_MIN = 0
PICTURE_MAX = 100

ADB_TIMEOUT = 15  # seconds


# ── Internal helpers ──────────────────────────────────────────────────────────

def _adb(*args: str, device: Optional[str] = None) -> subprocess.CompletedProcess:
    cmd = ["adb"]
    if device:
        cmd += ["-s", device]
    cmd += list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=ADB_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=124,
            stdout=exc.stdout.decode() if exc.stdout else "",
            stderr=f"adb timed out after {ADB_TIMEOUT}s: {' '.join(cmd)}",
        )


def _cms_tool(*args: str, device: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run CmsTool via app_process in shell (u:r:shell:s0) context."""
    shell_cmd = (
        f"CLASSPATH={_CLASSPATH} "
        f"app_process / CmsTool {' '.join(args)}"
    )
    return _adb("shell", shell_cmd, device=device)


# ── Public API ────────────────────────────────────────────────────────────────

def get_connected_devices() -> list[str]:
    """Return ADB serial numbers of all currently connected devices."""
    result = _adb("devices")
    devices = []
    for line in result.stdout.strip().splitlines()[1:]:
        if "\tdevice" in line:
            devices.append(line.split("\t")[0])
    return devices


def is_cms_tool_deployed(device: Optional[str] = None) -> bool:
    """Return True if cms_tool.dex is present on the device."""
    result = _adb("shell", f"ls {CMS_TOOL_DEVICE_PATH}", device=device)
    return result.returncode == 0


def push_cms_tool(device: Optional[str] = None) -> dict:
    """
    Push the pre-built cms_tool.dex to the TV.

    The DEX is read from hisense-cms-controller/tool/cms_tool.dex relative
    to the repo root.  Returns {"ok": bool, "stdout": str, "stderr": str}.
    """
    if not _LOCAL_DEX.exists():
        return {"ok": False, "stdout": "", "stderr": f"Local DEX not found: {_LOCAL_DEX}"}
    cmd = ["adb"]
    if device:
        cmd += ["-s", device]
    cmd += ["push", str(_LOCAL_DEX), CMS_TOOL_DEVICE_PATH]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "stdout": exc.stdout.decode() if exc.stdout else "",
            "stderr": f"adb timed out after 30s: {' '.join(cmd)}",
        }
    return {"ok": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr}


def set_cms_value(
    channel: str,
    control: str,
    value: int,
    device: Optional[str] = None,
) -> dict:
    """
    Set a single CMS control for one colour channel on the TV.

    Args:
        channel: One of CMS_CHANNELS keys (e.g. "Red", "Cyan").
        control: One of CMS_SET_ACTIONS keys ("Hue", "Saturation", "Brightness").
        value:   Integer in [-10, 10].
        device:  ADB device serial (optional).

    Returns:
        {"ok": bool, "stdout": str, "stderr": str}
    """
    if channel not in CMS_CHANNELS:
        raise ValueError(f"Unknown channel {channel!r}. Valid: {sorted(CMS_CHANNELS)}")
    if control not in CMS_SET_ACTIONS:
        raise ValueError(f"Unknown control {control!r}. Valid: {sorted(CMS_SET_ACTIONS)}")
    if not (CMS_MIN <= value <= CMS_MAX):
        raise ValueError(f"Value {value} out of range [{CMS_MIN}, {CMS_MAX}]")

    action = CMS_SET_ACTIONS[control]
    channel_id = CMS_CHANNELS[channel]

    result = _cms_tool(action, str(channel_id), str(value), device=device)
    ok = result.returncode == 0 and result.stdout.startswith("OK")
    return {"ok": ok, "stdout": result.stdout, "stderr": result.stderr}


def get_cms_value(
    channel: str,
    control: str,
    device: Optional[str] = None,
) -> dict:
    """
    Read a single CMS value from the TV.

    Returns:
        {"ok": bool, "value": int | None, "stdout": str, "stderr": str}
    """
    if channel not in CMS_CHANNELS:
        raise ValueError(f"Unknown channel {channel!r}. Valid: {sorted(CMS_CHANNELS)}")
    if control not in CMS_GET_ACTIONS:
        raise ValueError(f"Unknown control {control!r}. Valid: {sorted(CMS_GET_ACTIONS)}")

    action = CMS_GET_ACTIONS[control]
    channel_id = CMS_CHANNELS[channel]

    result = _cms_tool(action, str(channel_id), device=device)
    value = None
    ok = False
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("VALUE "):
                try:
                    value = int(line.split()[1])
                    ok = True
                except (IndexError, ValueError):
                    pass
    return {"ok": ok, "value": value, "stdout": result.stdout, "stderr": result.stderr}


def get_all_cms_values(device: Optional[str] = None) -> dict:
    """
    Read all CMS values (all 6 channels × 3 controls) from the TV.

    Returns:
        {"ok": bool, "values": {channel: {"hue": int, "sat": int, "bri": int}}, ...}
    """
    result = _cms_tool("get_all", device=device)
    values: dict[str, dict] = {}
    ok = False
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            # Format: "Red hue=5 sat=0 bri=0"
            parts = line.split()
            if len(parts) == 4 and parts[0] in CMS_CHANNELS:
                try:
                    values[parts[0]] = {
                        "hue": int(parts[1].split("=")[1]),
                        "sat": int(parts[2].split("=")[1]),
                        "bri": int(parts[3].split("=")[1]),
                    }
                except (IndexError, ValueError):
                    pass
        ok = len(values) == 6
    return {"ok": ok, "values": values, "stdout": result.stdout, "stderr": result.stderr}


def reset_cms(device: Optional[str] = None) -> dict:
    """
    Reset all CMS channels to 0 by setting each control to 0 explicitly.

    On the first timeout (returncode 124) the loop stops immediately and
    returns partial progress so the caller can retry just the failed slots.

    Returns:
        {"ok": bool, "stdout": str, "stderr": str}
        On partial timeout: adds "partial" (bool), "completed" (list[str]),
        and "failed_at" (str | None).
    """
    completed: list[str] = []
    errors = []
    for channel, channel_id in CMS_CHANNELS.items():
        for action in ("set_hue", "set_saturation", "set_brightness"):
            result = _cms_tool(action, str(channel_id), "0", device=device)
            if result.returncode == 124:
                return {
                    "ok": False,
                    "partial": True,
                    "completed": completed,
                    "failed_at": f"{channel}/{action}",
                    "stdout": "",
                    "stderr": result.stderr,
                }
            if not (result.returncode == 0 and result.stdout.startswith("OK")):
                errors.append(f"{channel}/{action}: {result.stderr or result.stdout}")
            completed.append(f"{channel}/{action}")
    ok = len(errors) == 0
    return {"ok": ok, "stdout": "" if ok else "\n".join(errors), "stderr": ""}


def set_picture_control(
    control: str,
    value: int,
    device: Optional[str] = None,
) -> dict:
    """
    Set a main picture control on the TV.

    Args:
        control: One of PICTURE_SET_ACTIONS keys ("Brightness", "Contrast",
                 "Saturation", "PictureMode").
        value:   Integer.  Brightness/Contrast/Saturation accept 0–100;
                 PictureMode is unconstrained (integer mode ID).
        device:  ADB device serial (optional).

    Returns:
        {"ok": bool, "stdout": str, "stderr": str}
    """
    if control not in PICTURE_SET_ACTIONS:
        raise ValueError(f"Unknown control {control!r}. Valid: {sorted(PICTURE_SET_ACTIONS)}")
    if control != "PictureMode" and not (PICTURE_MIN <= value <= PICTURE_MAX):
        raise ValueError(f"Value {value} out of range [{PICTURE_MIN}, {PICTURE_MAX}]")

    result = _cms_tool(PICTURE_SET_ACTIONS[control], str(value), device=device)
    ok = result.returncode == 0 and result.stdout.startswith("OK")
    return {"ok": ok, "stdout": result.stdout, "stderr": result.stderr}


def get_picture_control(
    control: str,
    device: Optional[str] = None,
) -> dict:
    """
    Read a main picture control value from the TV.

    Returns:
        {"ok": bool, "value": int | None, "stdout": str, "stderr": str}
    """
    if control not in PICTURE_GET_ACTIONS:
        raise ValueError(f"Unknown control {control!r}. Valid: {sorted(PICTURE_GET_ACTIONS)}")

    result = _cms_tool(PICTURE_GET_ACTIONS[control], device=device)
    value = None
    ok = False
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("VALUE "):
                try:
                    value = int(line.split()[1])
                    ok = True
                except (IndexError, ValueError):
                    pass
    return {"ok": ok, "value": value, "stdout": result.stdout, "stderr": result.stderr}


def get_adb_status(device: Optional[str] = None) -> dict:
    """
    Return a status dict describing the ADB connection and CmsTool state.

    Dict keys:
        connected (bool): At least one ADB device is reachable.
        devices (list[str]): Connected device serials.
        cms_tool_deployed (bool): cms_tool.dex present on target device.
        device (str | None): The serial that was queried (None = any device).
    """
    devices = get_connected_devices()
    connected = len(devices) > 0
    target = device or (devices[0] if devices else None)
    deployed = is_cms_tool_deployed(target) if connected else False
    return {
        "connected": connected,
        "devices": devices,
        "cms_tool_deployed": deployed,
        "device": target,
    }
