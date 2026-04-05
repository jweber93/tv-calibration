"""
Light Illusion Remote Control backend — stub implementation.

The Light Illusion Integration Protocol is documented in Customer Downloads
(requires a paid ColourSpace licence).  Once you have the protocol document:

  1. Obtain the Integration Protocol PDF + C++ source from:
       https://lightillusion.com/customer_downloads.html
     (or contact Steve Shaw at Light Illusion directly)

  2. ColourSpace must be set to Servant mode:
       Graph Options → Remote Control → Servant → Local  (same PC, port 20102)
       Graph Options → Remote Control → Servant → Remote (cross-network, specify IP)

  3. Implement send_command() below according to the protocol spec.

Current status: STUB — check_connection() works; trigger_measurement() always
returns {"ok": False} until the protocol is implemented.

Known facts about the protocol:
  - TCP port: 20102 (default; configurable)
  - Architecture: external "Master" connects to ColourSpace "Servant"
  - Capabilities: set patch colour/size/bit-depth, trigger probe measurement,
                  receive XYZ/xyY result
  - Real-world implementations: JVC AutoCal DLL, ADL4CS, Aulwa CS Remote
    (none have published source; see CS_autocal.zip at displaycalibrations.com)
"""

import logging
import socket

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 2.0  # seconds


def check_connection(host: str = "127.0.0.1", port: int = 20102) -> bool:
    """
    Attempt a TCP connection to the Remote Control port.
    Returns True if the port is open (ColourSpace is in Servant mode).
    """
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def send_command(sock: socket.socket, command: str) -> str:
    """
    Send a command string to ColourSpace and return the response.

    TODO: implement once the Integration Protocol format is known.
          The protocol is likely text-based (similar to the Network Server
          protocol) — replace this placeholder with the real framing.
    """
    raise NotImplementedError(
        "Remote Control protocol not yet implemented.  "
        "Obtain the Integration Protocol document from Light Illusion Customer Downloads "
        "and implement send_command() according to the spec."
    )


def trigger_measurement(host: str = "127.0.0.1", port: int = 20102) -> dict:
    """
    Connect to ColourSpace's Remote Control server and trigger one measurement.

    Returns {"ok": True, "method": "remote_control"} on success,
            {"ok": False, "error": ...} on failure.
    """
    if not check_connection(host, port):
        return {
            "ok": False,
            "error": (
                f"Cannot connect to ColourSpace Remote Control at {host}:{port}.  "
                "Make sure ZRO is open and set to Servant mode: "
                "Graph Options → Remote Control → Servant → Local."
            ),
        }

    # TODO: implement the actual protocol once documented.
    return {
        "ok": False,
        "error": (
            "Remote Control protocol not yet implemented.  "
            "Use backend = 'pyautogui' until the Integration Protocol "
            "document is obtained from Light Illusion Customer Downloads."
        ),
    }
