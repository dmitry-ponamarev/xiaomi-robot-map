"""Integration level tests: setup, entities, services, error handling."""

from __future__ import annotations

import base64

import pytest
from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.xiaomi_robot_map.cloud import XiaomiApiError, XiaomiAuthError
from custom_components.xiaomi_robot_map.const import DOMAIN, SERVICE_GET_MAP, SERVICE_REFRESH
from custom_components.xiaomi_robot_map.provider import MapFetcher

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
async def loaded_entry(hass, config_entry):
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


def _entities(hass, entry):
    registry = er.async_get(hass)
    return er.async_entries_for_config_entry(registry, entry.entry_id)


async def test_setup_creates_camera_and_sensors(hass, loaded_entry) -> None:
    entities = _entities(hass, loaded_entry)
    domains = {entity.domain for entity in entities}
    assert domains == {"camera", "sensor"}
    sensors = [entity for entity in entities if entity.domain == "sensor"]
    assert len(sensors) == 3
    camera = next(entity for entity in entities if entity.domain == "camera")
    assert camera.entity_id == "camera.robot_vacuum_map"
    assert loaded_entry.state is ConfigEntryState.LOADED


async def test_camera_returns_png(hass, loaded_entry) -> None:
    image = await async_get_image(hass, "camera.robot_vacuum_map")
    assert image.content_type == "image/png"
    assert image.content.startswith(PNG_MAGIC)


async def test_camera_attributes(hass, loaded_entry) -> None:
    state = hass.states.get("camera.robot_vacuum_map")
    assert state is not None
    attributes = state.attributes
    assert attributes["map_name"] == "1695678900"
    assert attributes["map_version"] == "3.5"
    assert len(attributes["calibration_points"]) == 3
    assert set(attributes["rooms"]) == {"1", "2", "3"}
    assert attributes["vacuum_position"]["x"] == 2600
    assert attributes["map_age"] >= 0
    assert len(attributes["path"]) == 6
    assert attributes["error"] is None


async def test_sensor_states(hass, loaded_entry) -> None:
    position = hass.states.get("sensor.robot_vacuum_position")
    assert position is not None
    assert position.state == "2600, 1500"
    assert position.attributes["angle"] == 45
    assert position.attributes["room"] == 1

    room = hass.states.get("sensor.robot_vacuum_current_room")
    assert room is not None
    assert room.state == "1"
    assert room.attributes["rooms_total"] == 3

    updated = hass.states.get("sensor.robot_vacuum_map_updated")
    assert updated is not None
    assert updated.state not in ("unknown", "unavailable")


async def test_room_names_from_options(hass, config_entry) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, options={"room_names": {"1": "Kitchen"}})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    room = hass.states.get("sensor.robot_vacuum_current_room")
    assert room is not None
    assert room.state == "Kitchen"


async def test_refresh_service(hass, loaded_entry) -> None:
    await hass.services.async_call(DOMAIN, SERVICE_REFRESH, {}, blocking=True)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_REFRESH)


async def test_get_map_service(hass, loaded_entry) -> None:
    response = await hass.services.async_call(
        DOMAIN, SERVICE_GET_MAP, {}, blocking=True, return_response=True
    )
    assert response["content_type"] == "image/png"
    assert base64.b64decode(response["image"]).startswith(PNG_MAGIC)
    assert response["attributes"]["map_index"] == 7
    assert response["map_name"] == "1695678900"


async def test_diagnostics(hass, loaded_entry) -> None:
    from homeassistant.components.diagnostics import async_redact_data  # noqa: F401

    from custom_components.xiaomi_robot_map.diagnostics import async_get_config_entry_diagnostics

    diagnostics = await async_get_config_entry_diagnostics(hass, loaded_entry)
    assert diagnostics["entry"]["data"]["password"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["token"] == "**REDACTED**"
    assert diagnostics["map"]["map_name"] == "1695678900"


async def test_fetch_failure_marks_entry_not_ready(hass, config_entry, monkeypatch) -> None:
    async def _fail(self):
        raise XiaomiApiError("cloud is down")

    monkeypatch.setattr(MapFetcher, "async_fetch", _fail)
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_failure_starts_reauth(hass, config_entry, monkeypatch) -> None:
    async def _fail(self, *args, **kwargs):
        raise XiaomiAuthError("bad credentials")

    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.XiaomiCloudClient.async_login", _fail)
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["handler"] == DOMAIN and flow["context"]["source"] == "reauth" for flow in flows)


async def test_unload_removes_services(hass, loaded_entry) -> None:
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, SERVICE_REFRESH)
    assert not hass.services.has_service(DOMAIN, SERVICE_GET_MAP)
