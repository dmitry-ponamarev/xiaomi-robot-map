"""Shared fixtures for the Xiaomi Robot Vacuum Map tests."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xiaomi_robot_map.cloud import CloudDevice, XiaomiCloudClient
from custom_components.xiaomi_robot_map.const import (
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.xiaomi_robot_map.provider import MapFetcher, MapPayload
from tests.fixtures.rrmap_builder import build_map

pytest_plugins = ["pytest_homeassistant_custom_component"]

DEVICE = CloudDevice(
    device_id="3123456789",
    name="Robot Vacuum",
    model="roborock.vacuum.s5",
    token="0123456789abcdef0123456789abcdef",
    country="de",
    user_id="42",
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components importable for every test."""
    yield


@pytest.fixture(autouse=True)
def fake_cloud(monkeypatch):
    """Replace the network layer with deterministic data."""
    monkeypatch.setattr("custom_components.xiaomi_robot_map.cloud.AVAILABLE_COUNTRIES", ["de"])

    async def _login(self, *args, **kwargs):
        self._service_token = "service-token"
        self._user_id = "42"

    async def _devices(self, countries=None):
        return [DEVICE]

    async def _fetch(self):
        return MapPayload(raw=build_map(), map_name="1695678900")

    monkeypatch.setattr(XiaomiCloudClient, "async_login", _login)
    monkeypatch.setattr(XiaomiCloudClient, "async_get_devices", _devices)
    monkeypatch.setattr(MapFetcher, "async_fetch", _fetch)


@pytest.fixture
def entry_data() -> dict:
    """Config entry data for a cloud configured vacuum."""
    return {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "secret",
        CONF_COUNTRY: "de",
        CONF_DEVICE_ID: "3123456789",
        CONF_MODEL: "roborock.vacuum.s5",
        CONF_NAME: "Robot Vacuum",
        CONF_TOKEN: "0123456789abcdef0123456789abcdef",
        CONF_HOST: "192.168.1.50",
        CONF_USER_ID: "42",
    }


@pytest.fixture
def config_entry(entry_data: dict) -> MockConfigEntry:
    """A config entry ready to be added to hass."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Robot Vacuum",
        unique_id="3123456789",
        data=entry_data,
    )
