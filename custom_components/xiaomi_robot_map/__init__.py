"""The Xiaomi Robot Vacuum Map integration."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import ConfigType

from .cloud import XiaomiCloudClient
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    CONF_BEARER_TOKEN,
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_LOCAL_KIND,
    CONF_MAP_NAME,
    CONF_PASSWORD,
    CONF_SOURCE,
    CONF_TOKEN,
    CONF_URL,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
    LOCAL_KIND_RRMAP,
    PLATFORMS,
    SERVICE_GET_MAP,
    SERVICE_REFRESH,
    SOURCE_LOCAL,
)
from .coordinator import MapCoordinator
from .provider import LocalMapFetcher, MapFetcher

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class XiaomiRobotMapRuntimeData:
    """Objects shared by all platforms of one config entry."""

    client: XiaomiCloudClient | None
    fetcher: MapFetcher | LocalMapFetcher
    coordinator: MapCoordinator


type XiaomiRobotMapConfigEntry = ConfigEntry[XiaomiRobotMapRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration (config entries only)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: XiaomiRobotMapConfigEntry) -> bool:
    """Set up one vacuum."""
    client: XiaomiCloudClient | None = None
    if entry.data.get(CONF_SOURCE) == SOURCE_LOCAL:
        fetcher: MapFetcher | LocalMapFetcher = LocalMapFetcher(
            hass,
            url=entry.data[CONF_URL],
            kind=entry.data.get(CONF_LOCAL_KIND, LOCAL_KIND_RRMAP),
            username=entry.data.get(CONF_USERNAME),
            password=entry.data.get(CONF_PASSWORD),
            token=entry.data.get(CONF_BEARER_TOKEN),
        )
    else:
        client = XiaomiCloudClient(
            None,
            username=entry.data.get(CONF_USERNAME),
            password=entry.data.get(CONF_PASSWORD),
        )
        fetcher = MapFetcher(
            hass,
            client,
            host=entry.data.get(CONF_HOST),
            token=entry.data.get(CONF_TOKEN, ""),
            country=entry.data.get(CONF_COUNTRY, "de"),
            user_id=entry.data.get(CONF_USER_ID),
            device_id=entry.data.get(CONF_DEVICE_ID),
            map_name_override=entry.options.get(CONF_MAP_NAME) or None,
        )
    coordinator = MapCoordinator(hass, entry, client, fetcher)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = XiaomiRobotMapRuntimeData(client=client, fetcher=fetcher, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await _async_register_services(hass)
    _LOGGER.debug("Map integration set up for %s (%s)", entry.title, entry.data.get("model"))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: XiaomiRobotMapConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    client = entry.runtime_data.client if entry.runtime_data else None
    if client is not None:
        await client.async_close()
    if unloaded and not _active_entries(hass):
        for service in (SERVICE_GET_MAP, SERVICE_REFRESH):
            hass.services.async_remove(DOMAIN, service)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: XiaomiRobotMapConfigEntry) -> None:
    """Reload the entry when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _active_entries(hass: HomeAssistant) -> list[XiaomiRobotMapConfigEntry]:
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):

        async def _async_handle_refresh(call: ServiceCall) -> None:
            for entry in _entries_for_call(hass, call):
                await entry.runtime_data.coordinator.async_force_refresh()

        hass.services.async_register(DOMAIN, SERVICE_REFRESH, _async_handle_refresh)

    if not hass.services.has_service(DOMAIN, SERVICE_GET_MAP):

        async def _async_handle_get_map(call: ServiceCall) -> ServiceResponse:
            entry = _entries_for_call(hass, call)[0]
            snapshot = entry.runtime_data.coordinator.data
            if snapshot is None:
                raise HomeAssistantError("No map has been rendered yet")
            return {
                "image": base64.b64encode(snapshot.png).decode(),
                "content_type": "image/png",
                "map_name": snapshot.map_name,
                "fetched_at": snapshot.fetched_at.isoformat(),
                "attributes": snapshot.map_data.as_dict(),
            }

        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_MAP,
            _async_handle_get_map,
            supports_response=SupportsResponse.ONLY,
        )


def _entries_for_call(hass: HomeAssistant, call: ServiceCall) -> list[XiaomiRobotMapConfigEntry]:
    entries = _active_entries(hass)
    if not entries:
        raise HomeAssistantError("No Xiaomi Robot Vacuum Map entry is loaded")
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise HomeAssistantError(f"Unknown config entry: {entry_id}")
        return [entry]
    return entries
