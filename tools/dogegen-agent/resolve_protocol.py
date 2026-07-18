"""
Client-side implementation of Light Illusion's "Resolve" pattern protocol —
the wire format Dogegen (https://github.com/ledoge/dogegen) speaks when
launched with `resolve_hdr`/`resolve_sdr`, and the same protocol ColourSpace
uses to drive Dogegen today (see issue #630).

There is no public protocol spec — Light Illusion documents it only for
paid-license integrators. The framing and XML shape below were read
directly out of Dogegen's open-source main.cpp (`StartResolve`,
`parseCalibrationXML`, `parseColorXML`, `parseGeometryXML`), not
reverse-engineered from a packet capture.

Connection direction: Dogegen is the TCP *client*. `resolve_hdr <ip[:port]>`
makes it dial out to `ip:port` (default `127.0.0.1:20002`) and then block
reading one framed patch command at a time, forever — it never sends
anything back. So the counterpart Dogegen expects to connect to (normally
ColourSpace) has to be a **server**; `ResolveServer` below is that server —
it accepts Dogegen's connection and pushes patches down it.

Wire format, byte for byte:
  - a 4-byte big-endian (network order) int32 giving the length of the XML
    payload that follows, then exactly that many bytes of XML
    (`recv(dataLen)`, `ntohl`, `recv(dataLen)` in StartResolve).
  - Dogegen does not parse the XML with a real parser — it scans for the
    literal substrings "<color", "<background", and "<geometry" anywhere in
    the payload (parseCalibrationXML), then reads `red=".."`, `green=".."`,
    `blue=".."`, `bits=".."` (color/background) or `x=".."`, `y=".."`,
    `cx=".."`, `cy=".."` (geometry) as quoted attributes on whichever tag it
    found. Tag order and nesting don't matter, and no XML declaration or
    single root element is required.
  - A tag Dogegen doesn't find is left at whatever value its variables
    already held (uninitialized, first time) rather than a defined default
    — so this module always emits all three tags on every message rather
    than relying on Dogegen's fallback for an omitted one.
  - `red`/`green`/`blue` are raw device-code integers at the stated bit
    depth (e.g. 0-1023 at bits=10), the same convention already used for
    patch RGB throughout this repo (calcore/patch_planner.py, the
    tools/reference/*_10bit_reference.csv fixtures) — Dogegen divides by
    `(1 << bits) - 1` itself (parseColorXML). `bits` must be 8 or 10;
    Dogegen silently ignores (logs and keeps showing the previous pattern)
    any other value for the foreground `<color>` tag, and there is no
    ack/nack on this protocol to report that back to us — so encode_patch_frame
    validates bits/channel ranges itself and raises before anything is sent.
"""

from __future__ import annotations

import logging
import math
import re
import socket
import struct
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Dogegen's own built-in default (main.cpp: `uint16_t port = 20002;`), used
# both as this module's default listen port and to decide whether a launch
# command needs an explicit host:port at all — see agent.py's
# _resolve_connect_target().
DEFAULT_RESOLVE_PORT = 20002

_VALID_BITS = (8, 10)

_LENGTH_STRUCT = struct.Struct("!i")  # network-order int32, matches ntohl(dataLen)


class PatchValidationError(ValueError):
    """A patch that Dogegen would silently drop or misrender: bad bit depth
    or a channel value out of range for it. Raised before anything is sent —
    the wire protocol has no ack/nack, so this is the only place to catch it."""


def _validate_channel(name: str, value: int, max_v: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PatchValidationError(f"{name} must be an int, got {value!r}")
    if not (0 <= value <= max_v):
        raise PatchValidationError(f"{name}={value} out of range for the given bit depth (0-{max_v})")


def _window_geometry(size_pct: float) -> Tuple[float, float, float, float]:
    """
    Centered square window of size_pct% of screen area, using the exact
    formula Dogegen's own `set_coords_from_window` uses for its launch-time
    `window_pct` CLI arg (main.cpp) — so a per-patch size_pct here produces
    the identical box a CLI window override would. Returns (x, y, cx, cy).
    """
    if not (0 < size_pct <= 100):
        raise PatchValidationError(f"size_pct must be >0 and <=100, got {size_pct!r}")
    num = math.sqrt(size_pct / 100)
    return ((1 - num) / 2, (1 - num) / 2, num, num)


def encode_patch_frame(
    r: int,
    g: int,
    b: int,
    *,
    bits: int = 8,
    size_pct: Optional[float] = None,
    x: float = 0.0,
    y: float = 0.0,
    cx: float = 1.0,
    cy: float = 1.0,
    bg: Tuple[int, int, int] = (0, 0, 0),
    bg_bits: Optional[int] = None,
) -> bytes:
    """
    Build one length-prefixed Resolve-protocol patch message for Dogegen.

    r/g/b/bg are raw device-code integers at the given bit depth (0-255 for
    bits=8, 0-1023 for bits=10) — see the module docstring.

    x/y/cx/cy are a fractional (0-1) rectangle: (0, 0, 1, 1), the default,
    is full-field and lets Dogegen's own launch-time window_pct control the
    on-screen box (see agent.py's dogegen_command_for_mode) — the same
    sizing the app already uses for HDR windows. Pass size_pct instead for
    a centered box of that size *for this patch specifically*, overriding
    x/y/cx/cy with the same math Dogegen's own window_pct uses (see
    _window_geometry) — e.g. a smaller window for near-black patches to
    control flare, independent of whatever window_pct Dogegen was launched
    with. size_pct takes precedence over x/y/cx/cy when both are given.

    Raises PatchValidationError for a bit depth or channel value Dogegen
    would silently reject or misrender, or an out-of-range size_pct.
    """
    if bits not in _VALID_BITS:
        raise PatchValidationError(f"bits must be 8 or 10, got {bits!r}")
    if bg_bits is None:
        bg_bits = bits
    if bg_bits not in _VALID_BITS:
        raise PatchValidationError(f"bg_bits must be 8 or 10, got {bg_bits!r}")

    max_v = (1 << bits) - 1
    bg_max_v = (1 << bg_bits) - 1
    _validate_channel("r", r, max_v)
    _validate_channel("g", g, max_v)
    _validate_channel("b", b, max_v)
    bg_r, bg_g, bg_b = bg
    _validate_channel("bg_r", bg_r, bg_max_v)
    _validate_channel("bg_g", bg_g, bg_max_v)
    _validate_channel("bg_b", bg_b, bg_max_v)

    if size_pct is not None:
        x, y, cx, cy = _window_geometry(size_pct)
    if cx <= 0 or cy <= 0:
        raise PatchValidationError(f"cx/cy must be >0, got cx={cx!r} cy={cy!r}")

    # No XML declaration or single root element needed — Dogegen scans for
    # these three tags as substrings (see module docstring), so plain
    # sibling self-closing tags are all it ever expects.
    xml = (
        f'<color red="{r}" green="{g}" blue="{b}" bits="{bits}"/>'
        f'<background red="{bg_r}" green="{bg_g}" blue="{bg_b}" bits="{bg_bits}"/>'
        f'<geometry x="{x}" y="{y}" cx="{cx}" cy="{cy}"/>'
    )
    payload = xml.encode("ascii")
    return _LENGTH_STRUCT.pack(len(payload)) + payload


_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _find_tag_attrs(xml: str, tag: str) -> Dict[str, str]:
    m = re.search(rf"<{tag}\b([^>]*)/?>", xml)
    if not m:
        raise ValueError(f"no <{tag}> tag found")
    return dict(_ATTR_RE.findall(m.group(1)))


def decode_patch_frame(frame: bytes) -> Dict[str, Any]:
    """
    Parse one length-prefixed Resolve-protocol message back into a dict —
    the inverse of encode_patch_frame. Used by tests to round-trip the
    encoder's output and for debug logging; Dogegen itself never sends this
    format back to us (see module docstring).
    """
    if len(frame) < _LENGTH_STRUCT.size:
        raise ValueError(f"frame too short for a length prefix ({len(frame)} bytes)")
    (length,) = _LENGTH_STRUCT.unpack_from(frame, 0)
    xml_bytes = frame[_LENGTH_STRUCT.size:]
    if len(xml_bytes) != length:
        raise ValueError(
            f"frame length prefix ({length}) does not match payload ({len(xml_bytes)} bytes)"
        )
    xml = xml_bytes.decode("ascii")

    color = _find_tag_attrs(xml, "color")
    background = _find_tag_attrs(xml, "background")
    geometry = _find_tag_attrs(xml, "geometry")

    return {
        "r": int(color["red"]),
        "g": int(color["green"]),
        "b": int(color["blue"]),
        "bits": int(color.get("bits", 8)),
        "bg": (int(background["red"]), int(background["green"]), int(background["blue"])),
        "bg_bits": int(background.get("bits", 8)),
        "x": float(geometry["x"]),
        "y": float(geometry["y"]),
        "cx": float(geometry["cx"]),
        "cy": float(geometry["cy"]),
    }


class ResolveServer:
    """
    Server side of Dogegen's Resolve-protocol connection.

    Dogegen (launched with resolve_hdr/resolve_sdr) is the TCP *client* — it
    dials out to a host:port and then blocks reading one framed patch
    command at a time, forever, sending nothing back (see module
    docstring). This class is the listener Dogegen was built to connect to
    (normally ColourSpace); once Dogegen has dialed in, send_patch() pushes
    one patch down that connection.

    Dogegen has no reconnect/backoff logic of its own and opens exactly one
    connection per resolve_hdr/resolve_sdr invocation, so this keeps
    accepting new connections for its entire lifetime — a Dogegen restart
    (POST /stop then /start) reconnects cleanly, replacing the old socket.

    Thread-safe: `_state_lock` guards the listen/connection sockets and can
    be acquired briefly by any thread; `_send_lock` is held for the
    duration of a send so two overlapping send_patch() calls can never
    interleave bytes on the wire (there's no per-message framing beyond the
    length prefix, so a torn write would desync every message after it).
    """

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_RESOLVE_PORT) -> None:
        self.host = host
        self.port = port
        self._state_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._listen_sock: Optional[socket.socket] = None
        self._conn: Optional[socket.socket] = None
        self._peer: Optional[str] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._running = False
        self._last_error: Optional[str] = None

    def start(self, *, raise_on_bind_fail: bool = False) -> None:
        """Start listening in the background. Best-effort by default: a bind
        failure (e.g. the port is already in use) is logged and recorded in
        status()['resolve_last_error'] rather than raised, so a bad
        resolve_listen_port doesn't take down the rest of the agent — that's
        what agent.py's main() relies on. Pass raise_on_bind_fail=True to get
        the OSError instead (e.g. in a test or a one-off script that wants to
        fail loudly on a bad port rather than silently not-listen)."""
        with self._state_lock:
            if self._running:
                return
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self.host, self.port))
                sock.listen(1)
            except OSError as exc:
                sock.close()
                self._last_error = f"Cannot listen on {self.host}:{self.port}: {exc}"
                logger.warning(self._last_error)
                if raise_on_bind_fail:
                    raise
                return
            self.port = sock.getsockname()[1]  # resolves port=0 to the actual ephemeral port
            sock.settimeout(1.0)  # periodic wake-up so stop() is noticed even if close() alone isn't
            self._listen_sock = sock
            self._running = True
            self._last_error = None
            self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._accept_thread.start()
            logger.info("Resolve server listening on %s:%s", self.host, self.port)

    def stop(self) -> None:
        with self._state_lock:
            self._running = False
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
                self._peer = None
            if self._listen_sock is not None:
                try:
                    self._listen_sock.close()
                except OSError:
                    pass
                self._listen_sock = None
        thread = self._accept_thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._accept_thread = None

    def _accept_loop(self) -> None:
        while True:
            with self._state_lock:
                sock = self._listen_sock
                running = self._running
            if not running or sock is None:
                return
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return  # listen socket closed out from under us by stop()
            with self._state_lock:
                if not self._running:
                    conn.close()
                    return
                if self._conn is not None:
                    try:
                        self._conn.close()
                    except OSError:
                        pass
                self._conn = conn
                self._peer = f"{addr[0]}:{addr[1]}"
                logger.info("Dogegen connected from %s", self._peer)

    def _drop_connection(self, conn: socket.socket) -> None:
        with self._state_lock:
            if self._conn is conn:
                try:
                    conn.close()
                except OSError:
                    pass
                self._conn = None
                self._peer = None

    def status(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "resolve_listening": self._listen_sock is not None,
                "resolve_connected": self._conn is not None,
                "resolve_peer": self._peer,
                "resolve_listen_port": self.port,
                "resolve_last_error": self._last_error,
            }

    def send_patch(self, *, timeout: float = 2.0, **kwargs: Any) -> Dict[str, Any]:
        """
        Encode and send one patch to Dogegen over the currently accepted
        connection.

        Returns {"ok": True} on success, or {"ok": False, "error": str,
        "error_type": "invalid_patch" | "not_connected" | "timeout" |
        "send_failed"} — never raises, never blocks longer than `timeout`.
        """
        try:
            frame = encode_patch_frame(**kwargs)
        except PatchValidationError as exc:
            return {"ok": False, "error_type": "invalid_patch", "error": str(exc)}

        with self._send_lock:
            with self._state_lock:
                conn = self._conn
            if conn is None:
                return {
                    "ok": False,
                    "error_type": "not_connected",
                    "error": (
                        "Dogegen is not connected to the Resolve server. Start Dogegen "
                        "with resolve_hdr/resolve_sdr pointed at this agent first "
                        "(POST /start)."
                    ),
                }
            try:
                conn.settimeout(timeout)
                conn.sendall(frame)
            except socket.timeout:
                self._drop_connection(conn)
                return {
                    "ok": False,
                    "error_type": "timeout",
                    "error": f"Timed out sending patch to Dogegen after {timeout}s.",
                }
            except OSError as exc:
                self._drop_connection(conn)
                return {"ok": False, "error_type": "send_failed", "error": f"Failed to send patch: {exc}"}

        return {"ok": True}
