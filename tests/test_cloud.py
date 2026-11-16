"""Tests for the Xiaomi cloud client helpers."""

from __future__ import annotations

import base64
import json

import pytest

from custom_components.xiaomi_robot_map.cloud import (
    CloudDevice,
    XiaomiCloudClient,
    _api_url,
    _decrypt_rc4,
    _enc_signature,
    _encrypt_rc4,
    _to_json,
)

URL = "https://de.api.io.mi.com/app/home/getmapfileurl"
SIGNED_NONCE = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def test_api_url() -> None:
    assert _api_url("cn") == "https://api.io.mi.com/app"
    assert _api_url("de") == "https://de.api.io.mi.com/app"
    assert _api_url("ru") == "https://ru.api.io.mi.com/app"


def test_to_json_strips_the_guard_prefix() -> None:
    payload = {"code": 0, "result": {"list": []}}
    assert _to_json("&&&START&&&" + json.dumps(payload)) == payload


def test_to_json_returns_empty_dict_for_garbage() -> None:
    assert _to_json("<html>oops</html>") == {}


def test_rc4_roundtrip() -> None:
    encrypted = _encrypt_rc4(SIGNED_NONCE, '{"obj_name":"1695678900"}')
    assert encrypted != '{"obj_name":"1695678900"}'
    assert _decrypt_rc4(SIGNED_NONCE, encrypted) == '{"obj_name":"1695678900"}'


def test_signature_matches_the_reference_implementation() -> None:
    """Values produced by the original Xiaomi cloud connector implementation."""
    params = {"data": '{"obj_name":"1695678900"}'}
    assert _enc_signature(URL, "POST", SIGNED_NONCE, params) == "bF/yYJ/h0I6nXQ8E01bd42E4WlI="

    params = {"data": '{"obj_name":"1695678900"}', "rc4_hash__": "abc"}
    assert _enc_signature(URL, "POST", SIGNED_NONCE, params) == "A7WG9BCr0JlDJ+SN4HSXhdHPTq8="


def test_client_requires_login() -> None:
    client = XiaomiCloudClient(session=None, username="user", password="secret")  # type: ignore[arg-type]
    assert client.logged_in is False


def test_supported_models() -> None:
    def device(model: str) -> CloudDevice:
        return CloudDevice(device_id="1", name="vac", model=model, token="t", country="de")

    assert device("roborock.vacuum.s5").is_supported
    assert device("rockrobo.vacuum.v1").is_supported
    assert not device("dreame.vacuum.p2008").is_supported
    assert not device("viomi.vacuum.v12").is_supported


def test_generate_nonce_is_unique_and_decodable() -> None:
    from custom_components.xiaomi_robot_map.cloud import _generate_nonce

    first = _generate_nonce(1700000000000)
    second = _generate_nonce(1700000000000)
    assert first != second
    assert len(base64.b64decode(first)) == 12


async def test_get_map_url_uses_the_result_url(monkeypatch) -> None:
    client = XiaomiCloudClient(session=None)  # type: ignore[arg-type]
    device = CloudDevice(
        device_id="1", name="vac", model="roborock.vacuum.s5", token="t", country="de", user_id="42"
    )
    calls: list[str] = []

    async def fake_api(country, endpoint, params):
        calls.append(endpoint)
        return {"result": {"url": "https://cdn/map"}}

    monkeypatch.setattr(client, "_async_api_call", fake_api)
    assert await client.async_get_map_url(device, "1695678900") == "https://cdn/map"
    assert calls == ["/home/getmapfileurl"]


async def test_get_map_url_falls_back_to_v2(monkeypatch) -> None:
    client = XiaomiCloudClient(session=None)  # type: ignore[arg-type]
    device = CloudDevice(
        device_id="1", name="vac", model="roborock.vacuum.s5", token="t", country="de", user_id="42"
    )
    calls: list[str] = []

    async def fake_api(country, endpoint, params):
        calls.append(endpoint)
        if endpoint == "/home/getmapfileurl":
            return {"result": None}
        return {"result": {"url": "https://cdn/v2"}}

    monkeypatch.setattr(client, "_async_api_call", fake_api)
    assert await client.async_get_map_url(device, "1695678900") == "https://cdn/v2"
    assert calls == ["/home/getmapfileurl", "/v2/home/get_interim_file_url"]


async def test_get_map_url_without_result(monkeypatch) -> None:
    client = XiaomiCloudClient(session=None)  # type: ignore[arg-type]
    device = CloudDevice(device_id="1", name="vac", model="roborock.vacuum.s5", token="t", country="de")

    async def fake_api(country, endpoint, params):
        return {}

    monkeypatch.setattr(client, "_async_api_call", fake_api)
    assert await client.async_get_map_url(device, "1") is None


async def test_download_rejects_empty_payload(monkeypatch) -> None:
    from custom_components.xiaomi_robot_map.cloud import XiaomiApiError

    class Response:
        status = 200

        async def read(self):
            return b""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    client = XiaomiCloudClient(session=Session())  # type: ignore[arg-type]
    with pytest.raises(XiaomiApiError):
        await client.async_download("https://cdn/map")
