"""
Tests for the direct-drive Resolve-protocol client (#630):
tools/dogegen-agent/resolve_protocol.py.

TestEncodePatchFrame/TestDecodePatchFrame cover the wire codec in isolation
(no sockets). TestResolveServer exercises real loopback sockets — a fake
"Dogegen" is just a plain socket.socket connecting in, matching how Dogegen
itself behaves (see resolve_protocol.py's module docstring): connect, then
read whatever's sent, forever, with nothing sent back.
"""

import socket
import struct
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "dogegen-agent"))

import resolve_protocol as rp  # noqa: E402


def _wait_until(predicate, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ── encode_patch_frame ───────────────────────────────────────────────────────

class TestEncodePatchFrame:
    def test_length_prefix_matches_payload(self):
        frame = rp.encode_patch_frame(512, 512, 512, bits=10)
        (declared_len,) = struct.unpack("!i", frame[:4])
        assert declared_len == len(frame) - 4

    def test_exact_wire_format_100_pct_white_10bit(self):
        # Fixture value straight from tools/reference/dogegen_hdr10_full_range_10bit_reference.csv
        # ("100% White,100,1023,1023,1023"). Locks in the exact tag/attribute
        # shape read from Dogegen's main.cpp (parseColorXML/parseGeometryXML)
        # -- see resolve_protocol.py's module docstring.
        frame = rp.encode_patch_frame(1023, 1023, 1023, bits=10)
        expected_xml = (
            '<color red="1023" green="1023" blue="1023" bits="10"/>'
            '<background red="0" green="0" blue="0" bits="10"/>'
            '<geometry x="0.0" y="0.0" cx="1.0" cy="1.0"/>'
        )
        expected = struct.pack("!i", len(expected_xml)) + expected_xml.encode("ascii")
        assert frame == expected

    def test_exact_wire_format_25_pct_gray_10bit(self):
        # "25% Gray,25,256,256,256" from the same reference CSV.
        frame = rp.encode_patch_frame(256, 256, 256, bits=10)
        expected_xml = (
            '<color red="256" green="256" blue="256" bits="10"/>'
            '<background red="0" green="0" blue="0" bits="10"/>'
            '<geometry x="0.0" y="0.0" cx="1.0" cy="1.0"/>'
        )
        expected = struct.pack("!i", len(expected_xml)) + expected_xml.encode("ascii")
        assert frame == expected

    def test_default_bits_is_8(self):
        decoded = rp.decode_patch_frame(rp.encode_patch_frame(255, 128, 0))
        assert decoded["bits"] == 8

    def test_background_defaults_to_black_matching_patch_bits(self):
        decoded = rp.decode_patch_frame(rp.encode_patch_frame(500, 500, 500, bits=10))
        assert decoded["bg"] == (0, 0, 0)
        assert decoded["bg_bits"] == 10

    def test_explicit_background(self):
        decoded = rp.decode_patch_frame(
            rp.encode_patch_frame(1023, 0, 0, bits=10, bg=(100, 100, 100), bg_bits=8)
        )
        assert decoded["bg"] == (100, 100, 100)
        assert decoded["bg_bits"] == 8

    def test_default_geometry_is_full_field(self):
        decoded = rp.decode_patch_frame(rp.encode_patch_frame(0, 0, 0))
        assert (decoded["x"], decoded["y"], decoded["cx"], decoded["cy"]) == (0.0, 0.0, 1.0, 1.0)

    def test_explicit_geometry(self):
        decoded = rp.decode_patch_frame(
            rp.encode_patch_frame(255, 255, 255, x=0.1, y=0.2, cx=0.3, cy=0.4)
        )
        assert (decoded["x"], decoded["y"], decoded["cx"], decoded["cy"]) == (0.1, 0.2, 0.3, 0.4)

    def test_size_pct_100_is_full_field(self):
        decoded = rp.decode_patch_frame(rp.encode_patch_frame(255, 255, 255, size_pct=100))
        assert decoded["x"] == pytest.approx(0.0)
        assert decoded["y"] == pytest.approx(0.0)
        assert decoded["cx"] == pytest.approx(1.0)
        assert decoded["cy"] == pytest.approx(1.0)

    def test_size_pct_matches_dogegens_window_formula(self):
        # main.cpp's set_coords_from_window: num = sqrt(windowSize / 100);
        # centered box of width/height `num`, i.e. top-left at (1-num)/2.
        decoded = rp.decode_patch_frame(rp.encode_patch_frame(255, 255, 255, size_pct=25))
        num = 0.5  # sqrt(25 / 100)
        assert decoded["cx"] == pytest.approx(num)
        assert decoded["cy"] == pytest.approx(num)
        assert decoded["x"] == pytest.approx((1 - num) / 2)
        assert decoded["y"] == pytest.approx((1 - num) / 2)

    def test_size_pct_overrides_explicit_geometry(self):
        decoded = rp.decode_patch_frame(
            rp.encode_patch_frame(1, 1, 1, size_pct=100, x=0.4, y=0.4, cx=0.1, cy=0.1)
        )
        assert (decoded["x"], decoded["y"], decoded["cx"], decoded["cy"]) == (0.0, 0.0, 1.0, 1.0)

    @pytest.mark.parametrize("bad_size", [0, -1, 100.1, 200])
    def test_size_pct_out_of_range_raises(self, bad_size):
        with pytest.raises(rp.PatchValidationError):
            rp.encode_patch_frame(0, 0, 0, size_pct=bad_size)

    @pytest.mark.parametrize("bad_bits", [1, 6, 9, 12, 16, 0])
    def test_invalid_bits_raises(self, bad_bits):
        with pytest.raises(rp.PatchValidationError, match="bits"):
            rp.encode_patch_frame(0, 0, 0, bits=bad_bits)

    def test_invalid_bg_bits_raises(self):
        with pytest.raises(rp.PatchValidationError, match="bg_bits"):
            rp.encode_patch_frame(0, 0, 0, bits=8, bg_bits=12)

    @pytest.mark.parametrize("channel", ["r", "g", "b"])
    def test_out_of_range_channel_raises(self, channel):
        values = {"r": 0, "g": 0, "b": 0}
        values[channel] = 256  # one past 8-bit max
        with pytest.raises(rp.PatchValidationError, match=channel):
            rp.encode_patch_frame(bits=8, **values)

    def test_negative_channel_raises(self):
        with pytest.raises(rp.PatchValidationError):
            rp.encode_patch_frame(-1, 0, 0, bits=8)

    def test_10bit_max_is_1023_not_255(self):
        # would silently clip/misrender on Dogegen's side if we let this through
        # scaled for the wrong bit depth
        rp.encode_patch_frame(1023, 1023, 1023, bits=10)  # must not raise
        with pytest.raises(rp.PatchValidationError):
            rp.encode_patch_frame(1024, 0, 0, bits=10)

    def test_out_of_range_background_channel_raises(self):
        with pytest.raises(rp.PatchValidationError, match="bg_g"):
            rp.encode_patch_frame(0, 0, 0, bits=8, bg=(0, 300, 0))

    @pytest.mark.parametrize("cx,cy", [(0, 1), (1, 0), (-1, 1), (1, -1)])
    def test_non_positive_size_raises(self, cx, cy):
        with pytest.raises(rp.PatchValidationError):
            rp.encode_patch_frame(0, 0, 0, cx=cx, cy=cy)


# ── decode_patch_frame ───────────────────────────────────────────────────────

class TestDecodePatchFrame:
    def test_round_trip(self):
        frame = rp.encode_patch_frame(700, 200, 900, bits=10, bg=(10, 20, 30), bg_bits=8,
                                       x=0.05, y=0.1, cx=0.5, cy=0.6)
        decoded = rp.decode_patch_frame(frame)
        assert decoded["r"] == 700
        assert decoded["g"] == 200
        assert decoded["b"] == 900
        assert decoded["bits"] == 10
        assert decoded["bg"] == (10, 20, 30)
        assert decoded["bg_bits"] == 8
        assert decoded["x"] == pytest.approx(0.05)
        assert decoded["y"] == pytest.approx(0.1)
        assert decoded["cx"] == pytest.approx(0.5)
        assert decoded["cy"] == pytest.approx(0.6)

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            rp.decode_patch_frame(b"\x00\x00")

    def test_length_mismatch_raises(self):
        frame = rp.encode_patch_frame(0, 0, 0)
        truncated = frame[:-1]
        with pytest.raises(ValueError, match="does not match"):
            rp.decode_patch_frame(truncated)


# ── ResolveServer ────────────────────────────────────────────────────────────

@pytest.fixture
def server():
    srv = rp.ResolveServer(host="127.0.0.1", port=0)
    srv.start()
    assert srv.status()["resolve_listening"] is True
    yield srv
    srv.stop()


def _connect_fake_dogegen(srv):
    """Open a plain client socket the way Dogegen itself would (see main.cpp's
    StartResolve: socket() + connect(), nothing fancier)."""
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect((srv.host if srv.host != "0.0.0.0" else "127.0.0.1", srv.port))
    assert _wait_until(lambda: srv.status()["resolve_connected"] is True)
    return client


def _recv_exact(sock, n, timeout=2.0):
    sock.settimeout(timeout)
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("peer closed before sending all expected bytes")
        data += chunk
    return data


def _recv_one_frame(sock, timeout=2.0):
    header = _recv_exact(sock, 4, timeout=timeout)
    (length,) = struct.unpack("!i", header)
    return header + _recv_exact(sock, length, timeout=timeout)


class TestResolveServerLifecycle:
    def test_not_connected_initially(self, server):
        status = server.status()
        assert status["resolve_connected"] is False
        assert status["resolve_peer"] is None

    def test_port_zero_resolves_to_real_ephemeral_port(self, server):
        assert server.port != 0

    def test_start_is_idempotent(self, server):
        server.start()  # must not raise or rebind
        assert server.status()["resolve_listening"] is True

    def test_accepting_connection_updates_status(self, server):
        client = _connect_fake_dogegen(server)
        try:
            status = server.status()
            assert status["resolve_connected"] is True
            assert status["resolve_peer"] is not None
        finally:
            client.close()

    def test_stop_closes_listener_and_connection(self, server):
        client = _connect_fake_dogegen(server)
        port = server.port
        server.stop()

        status = server.status()
        assert status["resolve_listening"] is False
        assert status["resolve_connected"] is False

        client.settimeout(2.0)
        assert client.recv(1) == b""  # server-side connection was closed

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        try:
            with pytest.raises(OSError):
                probe.connect(("127.0.0.1", port))  # nothing listens anymore
        finally:
            probe.close()
            client.close()

    def test_bind_failure_is_recorded_not_raised(self, server):
        # server (from the fixture) already owns its ephemeral port.
        other = rp.ResolveServer(host="127.0.0.1", port=server.port)
        other.start()  # must not raise
        try:
            status = other.status()
            assert status["resolve_listening"] is False
            assert status["resolve_last_error"] is not None
        finally:
            other.stop()

    def test_bind_failure_raises_when_requested(self, server):
        other = rp.ResolveServer(host="127.0.0.1", port=server.port)
        with pytest.raises(OSError):
            other.start(raise_on_bind_fail=True)
        # still recorded, same as the non-raising path, for callers that
        # catch the exception and want the message afterwards too
        assert other.status()["resolve_last_error"] is not None
        assert other.status()["resolve_listening"] is False
        other.stop()

    def test_reconnect_replaces_old_connection(self, server):
        client_a = _connect_fake_dogegen(server)
        peer_a = server.status()["resolve_peer"]

        client_b = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_b.connect(("127.0.0.1", server.port))
        assert _wait_until(lambda: server.status()["resolve_peer"] != peer_a)

        # old connection was closed server-side when the new one was accepted
        client_a.settimeout(2.0)
        assert client_a.recv(1) == b""

        result = server.send_patch(r=1, g=1, b=1, bits=8)
        assert result == {"ok": True}
        frame = _recv_one_frame(client_b)
        assert rp.decode_patch_frame(frame)["r"] == 1

        client_a.close()
        client_b.close()


class TestResolveServerSendPatch:
    def test_not_connected_returns_structured_error(self, server):
        result = server.send_patch(r=0, g=0, b=0)
        assert result["ok"] is False
        assert result["error_type"] == "not_connected"
        assert "not connected" in result["error"].lower()

    def test_invalid_patch_returns_structured_error_without_touching_socket(self, server):
        client = _connect_fake_dogegen(server)
        try:
            result = server.send_patch(r=0, g=0, b=0, bits=12)
            assert result == {
                "ok": False,
                "error_type": "invalid_patch",
                "error": "bits must be 8 or 10, got 12",
            }
            # connection must still be usable afterwards -- the bad call
            # never touched the socket.
            ok = server.send_patch(r=5, g=5, b=5, bits=8)
            assert ok == {"ok": True}
            frame = _recv_one_frame(client)
            assert rp.decode_patch_frame(frame)["r"] == 5
        finally:
            client.close()

    def test_send_delivers_exact_bytes_to_client(self, server):
        client = _connect_fake_dogegen(server)
        try:
            result = server.send_patch(r=1023, g=512, b=0, bits=10, bg=(1, 2, 3))
            assert result == {"ok": True}
            frame = _recv_one_frame(client)
            assert frame == rp.encode_patch_frame(1023, 512, 0, bits=10, bg=(1, 2, 3))
        finally:
            client.close()

    def test_multiple_sequential_patches_all_arrive_in_order(self, server):
        client = _connect_fake_dogegen(server)
        try:
            for i in range(5):
                assert server.send_patch(r=i, g=i, b=i, bits=8) == {"ok": True}
            for i in range(5):
                frame = _recv_one_frame(client)
                assert rp.decode_patch_frame(frame)["r"] == i
        finally:
            client.close()

    def test_concurrent_sends_do_not_interleave_bytes(self, server):
        client = _connect_fake_dogegen(server)
        results = []

        def _send(i):
            results.append(server.send_patch(r=i, g=0, b=0, bits=8))

        try:
            threads = [threading.Thread(target=_send, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

            assert all(r == {"ok": True} for r in results)
            received_r = set()
            for _ in range(10):
                frame = _recv_one_frame(client)
                received_r.add(rp.decode_patch_frame(frame)["r"])
            assert received_r == set(range(10))
        finally:
            client.close()

    def _swap_in_fake_conn(self, server, exc):
        """
        Real socket.socket instances don't allow patching individual bound
        methods (their C-level slots are read-only), so to deterministically
        exercise send_patch's except branches we substitute the whole
        connection object for a fake with the same duck-typed interface
        (settimeout/sendall/close) that send_patch actually calls -- no real
        timing or network fault injection needed.
        """
        fake_conn = MagicMock()
        fake_conn.sendall.side_effect = exc
        with server._state_lock:
            real_conn = server._conn
            server._conn = fake_conn
        return real_conn

    def test_send_timeout_drops_connection_and_reports_error(self, server):
        client = _connect_fake_dogegen(server)
        real_conn = self._swap_in_fake_conn(server, socket.timeout("timed out"))
        try:
            result = server.send_patch(r=0, g=0, b=0, timeout=0.01)
            assert result["ok"] is False
            assert result["error_type"] == "timeout"

            assert server.status()["resolve_connected"] is False
            # and a subsequent call correctly reports not_connected rather
            # than reusing the dead socket
            assert server.send_patch(r=0, g=0, b=0)["error_type"] == "not_connected"
        finally:
            real_conn.close()
            client.close()

    def test_send_os_error_drops_connection_and_reports_error(self, server):
        client = _connect_fake_dogegen(server)
        real_conn = self._swap_in_fake_conn(server, OSError("broken pipe"))
        try:
            result = server.send_patch(r=0, g=0, b=0)
            assert result["ok"] is False
            assert result["error_type"] == "send_failed"
            assert server.status()["resolve_connected"] is False
        finally:
            real_conn.close()
            client.close()
