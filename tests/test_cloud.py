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


class DummyJar:
    """Minimal cookie jar stand-in for client unit tests."""

    def update_cookies(self, *args, **kwargs) -> None:
        return None


class DummySession:
    """Minimal aiohttp session stand-in (no sockets, no loop)."""

    def __init__(self) -> None:
        self.cookie_jar = DummyJar()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


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
    client = XiaomiCloudClient(session=DummySession(), username="user", password="secret")  # type: ignore[arg-type]
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
    client = XiaomiCloudClient(session=DummySession())  # type: ignore[arg-type]
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
    client = XiaomiCloudClient(session=DummySession())  # type: ignore[arg-type]
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
    client = XiaomiCloudClient(session=DummySession())  # type: ignore[arg-type]
    device = CloudDevice(device_id="1", name="vac", model="roborock.vacuum.s5", token="t", country="de")

    async def fake_api(country, endpoint, params):
        return {}

    monkeypatch.setattr(client, "_async_api_call", fake_api)
    assert await client.async_get_map_url(device, "1") is None


async def test_captcha_answer_uses_capt_code_and_keeps_the_sign() -> None:
    """The captcha must be answered on the challenge that produced the image."""
    payload = (
        "&&&START&&&"
        '{"ssecurity":"ssecurity","userId":"42","cUserId":"c42","location":"https://loc"}'
    )

    class Response:
        status = 200

        def __init__(self, text: str) -> None:
            self._text = text

        async def text(self):
            return self._text

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Session:
        cookie_jar = DummyJar()

        def __init__(self) -> None:
            self.calls = []

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response(payload)

    session = Session()
    client = XiaomiCloudClient(session=session)  # type: ignore[arg-type]
    client._sign = "ORIGINAL-SIGN"

    await client._async_login_step_2("ABCD")

    _, kwargs = session.calls[-1]
    assert kwargs["params"]["captCode"] == "ABCD"
    assert kwargs["params"]["_sign"] == "ORIGINAL-SIGN"
    assert client._sign == "ORIGINAL-SIGN"


async def test_answer_captcha_never_requests_a_new_sign(monkeypatch) -> None:
    """A new ``_sign`` means a new challenge, so the typed code could not match."""
    from tests.conftest import REAL_ANSWER_CAPTCHA

    client = XiaomiCloudClient(session=DummySession())  # type: ignore[arg-type]
    monkeypatch.setattr(XiaomiCloudClient, "async_answer_captcha", REAL_ANSWER_CAPTCHA)
    client._sign = "ORIGINAL-SIGN"
    calls: list[object] = []

    async def _step_1() -> str:
        calls.append("step1")
        return "NEW-SIGN"

    async def _step_2(captcha_code=None) -> None:
        calls.append(("step2", captcha_code))

    async def _step_3() -> None:
        calls.append("step3")

    monkeypatch.setattr(client, "_async_login_step_1", _step_1)
    monkeypatch.setattr(client, "_async_login_step_2", _step_2)
    monkeypatch.setattr(client, "_async_login_step_3", _step_3)

    await client.async_answer_captcha("ABCD")

    assert calls == [("step2", "ABCD"), "step3"]
    assert client._sign == "ORIGINAL-SIGN"


async def test_answer_captcha_without_a_challenge_fails_cleanly(monkeypatch) -> None:
    from custom_components.xiaomi_robot_map.cloud import XiaomiCloudError
    from tests.conftest import REAL_ANSWER_CAPTCHA

    monkeypatch.setattr(XiaomiCloudClient, "async_answer_captcha", REAL_ANSWER_CAPTCHA)
    client = XiaomiCloudClient(session=DummySession())  # type: ignore[arg-type]
    with pytest.raises(XiaomiCloudError):
        await client.async_answer_captcha("ABCD")


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
