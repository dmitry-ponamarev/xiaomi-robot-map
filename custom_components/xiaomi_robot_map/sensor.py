"""Sensors describing the vacuum map."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ROOM_NAMES
from .coordinator import MapCoordinator
from .entity import XiaomiRobotMapEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MapCoordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            VacuumPositionSensor(coordinator, entry),
            VacuumRoomSensor(coordinator, entry),
            MapUpdatedSensor(coordinator, entry),
        ]
    )


class VacuumPositionSensor(XiaomiRobotMapEntity, SensorEntity):
    """Current position of the robot in map coordinates (millimetres)."""

    _attr_translation_key = "position"
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_position"

    @property
    def native_value(self) -> str | None:
        snapshot = self.coordinator.data
        if snapshot is None or snapshot.map_data.vacuum_position is None:
            return None
        position = snapshot.map_data.vacuum_position
        return f"{position.x}, {position.y}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self.coordinator.data
        if snapshot is None or snapshot.map_data.vacuum_position is None:
            return {}
        position = snapshot.map_data.vacuum_position
        return {
            "x": position.x,
            "y": position.y,
            "angle": position.angle,
            "room": snapshot.map_data.vacuum_room,
            "map_name": snapshot.map_name,
        }


class VacuumRoomSensor(XiaomiRobotMapEntity, SensorEntity):
    """Number of the room the robot currently is in."""

    _attr_translation_key = "current_room"
    _attr_icon = "mdi:floor-plan"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_current_room"

    @property
    def _room_names(self) -> dict[str, str]:
        names = self._entry.options.get(CONF_ROOM_NAMES) or {}
        if isinstance(names, dict):
            return {str(key): str(value) for key, value in names.items()}
        return {}

    @property
    def native_value(self) -> str | int | None:
        snapshot = self.coordinator.data
        if snapshot is None or snapshot.map_data.vacuum_room is None:
            return None
        room = snapshot.map_data.vacuum_room
        return self._room_names.get(str(room), room)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self.coordinator.data
        if snapshot is None:
            return {}
        return {
            "room_number": snapshot.map_data.vacuum_room,
            "room_name": self._room_names.get(str(snapshot.map_data.vacuum_room)),
            "rooms_total": len(snapshot.map_data.rooms),
            "rooms": {str(number): room.as_dict() for number, room in snapshot.map_data.rooms.items()},
        }


class MapUpdatedSensor(XiaomiRobotMapEntity, SensorEntity):
    """Timestamp of the last successful map update."""

    _attr_translation_key = "map_updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_map_updated"

    @property
    def native_value(self):
        snapshot = self.coordinator.data
        return snapshot.fetched_at if snapshot is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self.coordinator.data
        if snapshot is None:
            return {"error": self.coordinator.last_error}
        return {
            "map_name": snapshot.map_name,
            "raw_size": snapshot.raw_size,
            "error": self.coordinator.last_error,
        }
