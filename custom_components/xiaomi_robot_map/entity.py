"""Shared entity helpers."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MODEL, CONF_NAME, DOMAIN, MANUFACTURER
from .coordinator import MapCoordinator


def manufacturer_for_model(model: str | None) -> str:
    """Return a nicer manufacturer name for the device registry."""
    if model and model.startswith("roborock."):
        return "Roborock"
    return MANUFACTURER


class XiaomiRobotMapEntity(CoordinatorEntity[MapCoordinator]):
    """Base entity bound to the vacuum device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MapCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=manufacturer_for_model(entry.data.get(CONF_MODEL)),
            model=entry.data.get(CONF_MODEL),
            name=entry.data.get(CONF_NAME) or entry.title,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None
