"""Interoperability tests for the local miIO client.

python-miio is used here as an independent implementation of the protocol: the
packets our client builds must be parseable by it, and the packets it builds
must be parseable by our client.  The module is a test-only dependency, the
integration itself does not need it.
"""

from __future__ import annotations

import datetime
import socket
import struct
import threading

import pytest
import pytest_socket

miio_protocol = pytest.importorskip("miio.protocol")

# These tests talk to a local UDP socket on 127.0.0.1.
pytestmark = pytest.mark.enable_socket

from custom_components.xiaomi_robot_map import miio_local  # noqa: E402

TOKEN_HEX = "0123456789abcdef0123456789abcdef"
TOKEN = bytes.fromhex(TOKEN_HEX)
DEVICE_ID = b"\x01\x02\x03\x04"


def test_encryption_matches_reference() -> None:
    payload = b'{"id":1,"method":"get_map_v1","params":[]}\x00'
    assert miio_local.encrypt(payload, TOKEN) == miio_protocol.Utils.encrypt(payload, TOKEN)
    assert miio_local.decrypt(miio_local.encrypt(payload, TOKEN), TOKEN) == payload


def test_command_is_parseable_by_the_reference() -> None:
    packet = miio_local.build_command(
        DEVICE_ID, 12345, {"id": 7, "method": "get_map_v1", "params": []}, TOKEN
    )
    parsed = miio_protocol.Message.parse(packet, token=TOKEN)
    assert parsed.data.value == {"id": 7, "method": "get_map_v1", "params": []}
    assert parsed.header.value.device_id == DEVICE_ID
    assert parsed.header.value.length == len(packet)


def test_response_from_the_reference_is_parseable() -> None:
    payload = {"id": 1, "result": ["1695678900"], "error": {"code": 0}}
    packet = miio_protocol.Message.build(
        {
            "data": {"value": payload},
            "header": {
                "value": {
                    "length": 0,
                    "unknown": 0,
                    "device_id": DEVICE_ID,
                    "ts": datetime.datetime.utcnow(),
                }
            },
            "checksum": 0,
        },
        token=TOKEN,
    )
    assert miio_local.parse_command(packet, TOKEN) == payload


def test_parse_handshake() -> None:
    data = struct.pack(">HHI", 0x2131, 32, 0) + DEVICE_ID + struct.pack(">I", 777) + TOKEN
    device_id, timestamp = miio_local.parse_handshake(data)
    assert device_id == DEVICE_ID
    assert timestamp == 777
    with pytest.raises(miio_local.MiioProtocolError):
        miio_local.parse_handshake(b"\x00" * 32)


def test_bad_checksum_is_rejected() -> None:
    packet = bytearray(miio_local.build_command(DEVICE_ID, 1, {"id": 1, "method": "x", "params": []}, TOKEN))
    packet[-1] ^= 0xFF
    with pytest.raises(miio_local.MiioProtocolError):
        miio_local.parse_command(bytes(packet), TOKEN)


def test_extract_map_name() -> None:
    assert miio_local.extract_map_name(["1695678900"]) == "1695678900"
    assert miio_local.extract_map_name("1695678900") == "1695678900"
    assert miio_local.extract_map_name([b" 1695678900 "]) == "1695678900"
    assert miio_local.extract_map_name(["retry"]) is None
    assert miio_local.extract_map_name([]) is None
    assert miio_local.extract_map_name(None) is None


class FakeVacuum(threading.Thread):
    """UDP server that speaks the same protocol as a Roborock vacuum."""

    def __init__(self, result: object = None) -> None:
        super().__init__(daemon=True)
        # The HA test plugin disables sockets in an autouse fixture.
        pytest_socket.enable_socket()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.result = result if result is not None else {"id": 1, "result": ["1695678900"], "error": {"code": 0}}
        self.requests: list[dict] = []
        self.errors: list[Exception] = []
        self._stop = threading.Event()

    def run(self) -> None:
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            if data == miio_local.HANDSHAKE:
                header = struct.pack(">HHI", 0x2131, 32, 0) + DEVICE_ID + struct.pack(">I", 1000)
                self.sock.sendto(header + TOKEN, addr)
                continue
            try:
                message = miio_protocol.Message.parse(data, token=TOKEN)
            except Exception as err:  # noqa: BLE001 - surfaced through self.errors
                self.errors.append(err)
                continue
            self.requests.append(message.data.value)
            reply = miio_protocol.Message.build(
                {
                    "data": {"value": self.result},
                    "header": {
                        "value": {
                            "length": 0,
                            "unknown": 0,
                            "device_id": DEVICE_ID,
                            "ts": datetime.datetime.utcnow(),
                        }
                    },
                    "checksum": 0,
                },
                token=TOKEN,
            )
            self.sock.sendto(reply, addr)

    def stop(self) -> None:
        self._stop.set()
        self.sock.close()


@pytest.fixture
def vacuum():
    device = FakeVacuum()
    device.start()
    try:
        yield device
    finally:
        device.stop()
        device.join(timeout=2)


def test_get_map_name_end_to_end(vacuum) -> None:
    assert miio_local.get_map_name("127.0.0.1", TOKEN_HEX, timeout=2, port=vacuum.port) == "1695678900"
    assert vacuum.errors == []
    assert vacuum.requests[0]["method"] == "get_map_v1"
    assert vacuum.requests[0]["params"] == []


def test_retry_result_raises() -> None:
    device = FakeVacuum(result={"id": 1, "result": ["retry"], "error": {"code": 0}})
    device.start()
    try:
        with pytest.raises(miio_local.MiioProtocolError):
            miio_local.get_map_name("127.0.0.1", TOKEN_HEX, timeout=2, port=device.port)
    finally:
        device.stop()
        device.join(timeout=2)


def test_error_result_raises() -> None:
    device = FakeVacuum(result={"id": 1, "error": {"code": -9999, "message": "busy"}})
    device.start()
    try:
        with pytest.raises(miio_local.MiioProtocolError):
            miio_local.send_command("127.0.0.1", TOKEN, "get_map_v1", timeout=2, port=device.port)
    finally:
        device.stop()
        device.join(timeout=2)


def test_timeout() -> None:
    pytest_socket.enable_socket()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens there any more
    with pytest.raises(miio_local.MiioError):
        miio_local.get_map_name("127.0.0.1", TOKEN_HEX, timeout=0.3, port=port)


def test_invalid_token() -> None:
    with pytest.raises(ValueError):
        miio_local.get_map_name("127.0.0.1", "not-hex", timeout=0.1)
