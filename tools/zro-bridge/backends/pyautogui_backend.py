"""
PyAutoGUI backend — find the ZRO window and trigger a measurement via
UI automation (bring to foreground, send a keypress or mouse click).

This backend requires no special credentials and works with ZRO out of
the box.  The only configuration needed is:
  - zro_window_title  (partial match, case-insensitive; default "ColourSpace")
  - measure_action    "key" or "click"
  - measure_key       e.g. "space" (used when measure_action == "key")
  - measure_coords    [x, y] screen coords (used when measure_action == "click")

Finding the right key / coordinates:
  In ZRO's Manual Measure window, the "Measure" button can usually be
  triggered by clicking it or pressing a keyboard shortcut.  To find the
  right approach:
    1. Open ZRO and navigate to a measurement screen.
    2. Run `python -c "import pyautogui; import time; time.sleep(3); print(pyautogui.position())"`,
       move your cursor over the Measure button, and note the coordinates.
    3. Set measure_action = "click" and measure_coords = [x, y] in bridge.json.
  Alternatively, try measure_action = "key" with measure_key = "space" or "return"
  and see if ZRO responds.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# These imports only succeed on Windows with the packages installed.
# Fail gracefully so the bridge can still start and report status.
try:
    import pygetwindow as gw  # type: ignore
    _GW_AVAILABLE = True
except ImportError:
    _GW_AVAILABLE = False
    logger.warning("pygetwindow not installed — window detection unavailable")

try:
    import pyautogui  # type: ignore
    pyautogui.FAILSAFE = True   # move mouse to top-left corner to abort
    pyautogui.PAUSE = 0.05
    _PAG_AVAILABLE = True
except ImportError:
    _PAG_AVAILABLE = False
    logger.warning("pyautogui not installed — UI automation unavailable")


def find_zro_window(window_title: str = "ColourSpace"):
    """
    Return the first window whose title contains *window_title* (case-insensitive),
    or None if not found.
    """
    if not _GW_AVAILABLE:
        return None
    title_lower = window_title.lower()
    matches = [w for w in gw.getAllWindows() if title_lower in w.title.lower()]
    if not matches:
        logger.debug("No window matching %r found", window_title)
        return None
    return matches[0]


def _focus_window(win) -> bool:
    """Bring *win* to the foreground.  Returns True on success."""
    try:
        if win.isMinimized:
            win.restore()
            time.sleep(0.1)
        win.activate()
        return True
    except Exception as exc:
        logger.warning("Could not focus window %r: %s", win.title, exc)
        # Some pygetwindow versions raise on activate() even when it works.
        return True


def trigger_measurement(
    window_title: str = "ColourSpace",
    action: str = "key",
    key: str = "space",
    coords: Optional[list] = None,
    pre_delay_ms: int = 250,
) -> dict:
    """
    Trigger a ZRO measurement via UI automation.

    Returns {"ok": True, "method": ...} on success,
            {"ok": False, "error": ...} on failure.
    """
    if not _GW_AVAILABLE or not _PAG_AVAILABLE:
        missing = []
        if not _GW_AVAILABLE:
            missing.append("pygetwindow")
        if not _PAG_AVAILABLE:
            missing.append("pyautogui")
        return {"ok": False, "error": f"Missing packages: {', '.join(missing)}"}

    win = find_zro_window(window_title)
    if win is None:
        return {"ok": False, "error": f"ZRO window not found (looking for {window_title!r})"}

    logger.info("Found ZRO window: %r", win.title)

    if not _focus_window(win):
        return {"ok": False, "error": f"Could not focus window {win.title!r}"}

    # Wait for focus to settle.
    time.sleep(pre_delay_ms / 1000.0)

    if action == "key":
        if not key:
            return {"ok": False, "error": "measure_key is not configured"}
        logger.info("Pressing key %r in ZRO window", key)
        pyautogui.press(key)
        return {"ok": True, "method": "key", "key": key, "window": win.title}

    elif action == "click":
        if not coords or len(coords) < 2:
            return {"ok": False, "error": "measure_coords [x, y] is not configured"}
        x, y = int(coords[0]), int(coords[1])
        logger.info("Clicking ZRO at (%d, %d)", x, y)
        pyautogui.click(x, y)
        return {"ok": True, "method": "click", "coords": [x, y], "window": win.title}

    else:
        return {"ok": False, "error": f"Unknown measure_action: {action!r}. Use 'key' or 'click'."}
