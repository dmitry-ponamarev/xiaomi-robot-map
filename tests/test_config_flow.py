"""Tests for the config and options flows."""

from __future__ import annotations

import json

from custom_components.xiaomi_robot_map.cloud import CloudDevice, XiaomiAuthError, XiaomiCloudError
from custom_components.xiaomi_robot_map.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_LAYERS,
    CONF_LOCAL_KIND,
    CONF_PASSWORD,
    CONF_SCALE,
    CONF_SCAN_INTERVAL,
    CONF_SOURCE,
    CONF_TOKEN,
    CONF_URL,
    CONF_USERNAME,
    DOMAIN,
)
from tests.fixtures.valetudo_builder import build_valetudo_map

USER_INPUT = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_COUNTRY: "de",
}

VALETUDO_URL = "http://192.168.1.90/api/v2/robot/state/map"
LOCAL_INPUT = {
    CONF_LOCAL_KIND: "valetudo",
    CONF_URL: VALETUDO_URL,
    CONF_USERNAME: "",
    CONF_PASSWORD: "",
    "bearer_token": "",
    "name": "Kitchen robot",
}


async def _start(hass, source: str = "cloud"):
    """Start the flow and pick the map source."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_SOURCE: source})


async def test_source_form(hass) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_cloud_flow_creates_entry(hass) -> None:
    result = await _start(hass, "cloud")
    assert result["step_id"] == "cloud"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == "form"
    assert result["step_id"] == "device"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "3123456789", CONF_HOST: "192.168.1.50"}
    )
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.title == "Robot Vacuum"
    assert entry.unique_id == "3123456789"
    assert entry.data[CONF_TOKEN] == "0123456789abcdef0123456789abcdef"
    assert entry.data[CONF_HOST] == "192.168.1.50"
    assert entry.data["model"] == "roborock.vacuum.s5"
    assert entry.data[CONF_SOURCE] == "cloud"


async def test_invalid_auth(hass, monkeypatch) -> None:
    async def _login(self, *args, **kwargs):
        raise XiaomiAuthError("bad credentials")

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_login", _login)
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cloud_unreachable(hass, monkeypatch) -> None:
    async def _login(self, *args, **kwargs):
        raise XiaomiCloudError("timeout")

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_login", _login)
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_captcha_flow(hass, monkeypatch) -> None:
    """Xiaomi asks for a captcha: the form shows the image and accepts the code."""
    from custom_components.xiaomi_robot_map.cloud import XiaomiCaptchaRequired

    async def _login(self, captcha_code=None):
        if captcha_code != "abcd":
            raise XiaomiCaptchaRequired("/pass/getCode?icodeType=login&0.1")

    async def _captcha(self, url):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 24

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_login", _login)
    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_fetch_captcha", _captcha)

    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == "form"
    assert result["step_id"] == "captcha"
    placeholders = result["description_placeholders"]
    image_url = placeholders["image_url"]
    assert "/api/xiaomi_robot_map/captcha/" in image_url
    assert placeholders["image_markdown"] == f"![captcha]({image_url})"
    from custom_components.xiaomi_robot_map.http_views import _storage

    stored = _storage(hass)
    assert list(stored.values())[-1][1] == "image/png"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"captcha_code": "abcd"})
    assert result["step_id"] == "device"


async def test_captcha_rejected(hass, monkeypatch) -> None:
    from custom_components.xiaomi_robot_map.cloud import XiaomiCaptchaRequired

    async def _login(self, captcha_code=None):
        raise XiaomiCaptchaRequired("/pass/getCode?icodeType=login&0.1")

    async def _captcha(self, url):
        return b"\xff\xd8" + b"0" * 24

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_login", _login)
    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_fetch_captcha", _captcha)

    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"captcha_code": "zzzz"})
    assert result["type"] == "form"
    assert result["step_id"] == "captcha"
    assert result["errors"] == {"base": "invalid_captcha"}
    assert result["description_placeholders"]["image_markdown"].startswith("![captcha](")
    from custom_components.xiaomi_robot_map.http_views import _storage

    assert list(_storage(hass).values())[-1][1] == "image/jpeg"


async def test_invalid_auth_shows_xiaomi_code(hass, monkeypatch) -> None:
    async def _login(self, *args, **kwargs):
        raise XiaomiAuthError("登录验证失败", code=70016, description="登录验证失败")

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_login", _login)
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "invalid_auth"}
    assert result["description_placeholders"]["detail"] == "Xiaomi: 70016: 登录验证失败"


async def test_no_devices(hass, monkeypatch) -> None:
    async def _devices(self, countries=None):
        return []

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_get_devices", _devices)
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == "form"
    # The flow still offers the manual (host/token) fallback.
    assert result["step_id"] == "device"


async def test_manual_flow(hass) -> None:
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "__manual__", CONF_HOST: "192.168.1.50"}
    )
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "192.168.1.51", CONF_TOKEN: "0123456789ABCDEF0123456789ABCDEF", "name": "Kitchen robot"},
    )
    assert result["type"] == "create_entry"
    assert result["result"].title == "Kitchen robot"
    assert result["result"].data[CONF_TOKEN] == "0123456789abcdef0123456789abcdef"


async def test_manual_flow_validates_token(hass) -> None:
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "__manual__", CONF_HOST: "192.168.1.50"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.51", CONF_TOKEN: "not-a-token"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {CONF_TOKEN: "invalid_token"}


async def test_manual_flow_unknown_device(hass, monkeypatch) -> None:
    async def _devices(self, countries=None):
        return [CloudDevice(device_id="other", name="Other", model="roborock.vacuum.s5", token="f" * 32, country="de")]

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_get_devices", _devices)
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "__manual__", CONF_HOST: "192.168.1.50"}
    )
    # The device list is fetched again inside the manual step.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.51", CONF_TOKEN: "0123456789abcdef0123456789abcdef"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "device_not_found"}


async def test_duplicate_entry_aborts(hass) -> None:
    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "3123456789", CONF_HOST: "192.168.1.50"}
    )
    assert result["type"] == "create_entry"

    result = await _start(hass, "cloud")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "3123456789", CONF_HOST: "192.168.1.60"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


# -- local source (no cloud) ----------------------------------------------


async def test_local_flow_creates_entry(hass, aioclient_mock) -> None:
    aioclient_mock.get(VALETUDO_URL, text=json.dumps(build_valetudo_map()))

    result = await _start(hass, "local")
    assert result["type"] == "form"
    assert result["step_id"] == "local"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOCAL_INPUT)
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.title == "Kitchen robot"
    assert entry.data[CONF_SOURCE] == "local"
    assert entry.data[CONF_URL] == VALETUDO_URL
    assert entry.data[CONF_LOCAL_KIND] == "valetudo"


async def test_local_flow_rejects_bad_url(hass) -> None:
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**LOCAL_INPUT, CONF_URL: "192.168.1.90/api"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_local_flow_unreachable(hass, aioclient_mock) -> None:
    aioclient_mock.get(VALETUDO_URL, status=401)
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOCAL_INPUT)
    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_local_flow_invalid_document(hass, aioclient_mock) -> None:
    aioclient_mock.get(VALETUDO_URL, text="<html>not a map</html>")
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOCAL_INPUT)
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_format"}


async def test_local_raw_rrmap(hass, aioclient_mock) -> None:
    from tests.fixtures.rrmap_builder import build_map

    url = "http://192.168.1.90/map.gz"
    aioclient_mock.get(url, content=build_map())
    result = await _start(hass, "local")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**LOCAL_INPUT, CONF_LOCAL_KIND: "rrmap", CONF_URL: url},
    )
    assert result["type"] == "create_entry"
    assert result["result"].data[CONF_LOCAL_KIND] == "rrmap"


async def test_options_flow(hass, config_entry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 15,
            CONF_SCALE: "2.0",
            CONF_LAYERS: ["robot", "charger", "path"],
            "room_names": "1: Kitchen, 2: Hall",
            "trim": "5,0,5,0",
        },
    )
    assert result["type"] == "create_entry"
    options = result["data"]
    assert options[CONF_SCAN_INTERVAL] == 15
    assert options[CONF_SCALE] == "2.0"
    assert options[CONF_LAYERS] == ["robot", "charger", "path"]
    assert options["room_names"] == {"1": "Kitchen", "2": "Hall"}
    assert options["trim"] == "5,0,5,0"


async def test_options_ignores_garbage_trim(hass, config_entry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"trim": "a,b,c,d"})
    assert result["data"]["trim"] == "0,0,0,0"


async def test_captcha_view_serves_the_image(hass, hass_client, hass_access_token) -> None:
    from homeassistant.setup import async_setup_component

    from custom_components.xiaomi_robot_map.http_views import (
        async_register_captcha_view,
        store_captcha,
    )

    assert await async_setup_component(hass, "http", {})
    async_register_captcha_view(hass)
    store_captcha(hass, "flow-1", b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    client = await hass_client()
    headers = {"Authorization": f"Bearer {hass_access_token}"}
    response = await client.get("/api/xiaomi_robot_map/captcha/flow-1", headers=headers)
    assert response.status == 200
    assert response.content_type == "image/png"
    assert (await response.read()).startswith(b"\x89PNG")

    missing = await client.get("/api/xiaomi_robot_map/captcha/nope", headers=headers)
    assert missing.status == 404

    from custom_components.xiaomi_robot_map.http_views import captcha_url

    signed = captcha_url(hass, "flow-1")
    assert "/api/xiaomi_robot_map/captcha/flow-1" in signed
    assert "authSig=" in signed
    # the signed URL must work without an Authorization header (used in <img>)
    from urllib.parse import urlparse

    parsed = urlparse(signed)
    response = await client.get(f"{parsed.path}?{parsed.query}")
    assert response.status == 200


async def test_captcha_view_needs_auth(hass, hass_client_no_auth) -> None:
    from homeassistant.setup import async_setup_component

    from custom_components.xiaomi_robot_map.http_views import (
        async_register_captcha_view,
        store_captcha,
    )

    assert await async_setup_component(hass, "http", {})
    async_register_captcha_view(hass)
    store_captcha(hass, "flow-2", b"jpegdata")
    client = await hass_client_no_auth()
    response = await client.get("/api/xiaomi_robot_map/captcha/flow-2")
    assert response.status in (401, 403)


async def test_reauth_flow(hass, config_entry, monkeypatch) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": config_entry.entry_id, "unique_id": config_entry.unique_id},
    )
    assert result["step_id"] == "reauth_confirm"

    async def _devices(self, countries=None):
        return [
            CloudDevice(
                device_id="3123456789",
                name="Robot Vacuum",
                model="roborock.vacuum.s5",
                token="f" * 32,
                country="de",
                user_id="42",
            )
        ]

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_get_devices", _devices)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_TOKEN] == "f" * 32
