"""Tests for the config and options flows."""

from __future__ import annotations

from custom_components.xiaomi_robot_map.cloud import CloudDevice, XiaomiAuthError, XiaomiCloudError
from custom_components.xiaomi_robot_map.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_LAYERS,
    CONF_PASSWORD,
    CONF_SCALE,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USERNAME,
    DOMAIN,
)

USER_INPUT = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_COUNTRY: "de",
}


async def _start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def test_user_form(hass) -> None:
    result = await _start(hass)
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_cloud_flow_creates_entry(hass) -> None:
    result = await _start(hass)
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


async def test_invalid_auth(hass, monkeypatch) -> None:
    async def _login(self, *args, **kwargs):
        raise XiaomiAuthError("bad credentials")

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_login", _login)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cloud_unreachable(hass, monkeypatch) -> None:
    async def _login(self, *args, **kwargs):
        raise XiaomiCloudError("timeout")

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_login", _login)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_no_devices(hass, monkeypatch) -> None:
    async def _devices(self, countries=None):
        return []

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_get_devices", _devices)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == "form"
    # The flow still offers the manual (host/token) fallback.
    assert result["step_id"] == "device"


async def test_manual_flow(hass) -> None:
    result = await _start(hass)
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
    result = await _start(hass)
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
    result = await _start(hass)
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
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "3123456789", CONF_HOST: "192.168.1.50"}
    )
    assert result["type"] == "create_entry"

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: "3123456789", CONF_HOST: "192.168.1.60"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


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
