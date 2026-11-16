"""Camera platform exposing the rendered vacuum map."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import MapCoordinator
from .entity import XiaomiRobotMapEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MapCoordinator = entry.runtime_data.coordinator
    async_add_entities([VacuumMapCamera(coordinator, entry)])


class VacuumMapCamera(XiaomiRobotMapEntity, Camera):
    """A PNG camera showing the vacuum map."""

    _attr_translation_key = "map"
    _attr_content_type = "image/png"
    _attr_supported_features = CameraEntityFeature(0)

    def __init__(self, coordinator: MapCoordinator, entry: ConfigEntry) -> None:
        XiaomiRobotMapEntity.__init__(self, coordinator, entry)
        Camera.__init__(self)
        self.content_type = "image/png"
        self._attr_unique_id = f"{entry.entry_id}_map"
        self._last_image: bytes | None = None

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        snapshot = self.coordinator.data
        if snapshot is None:
            return self._last_image
        self._last_image = snapshot.png
        return snapshot.png

    def _handle_coordinator_update(self) -> None:
        snapshot = self.coordinator.data
        if snapshot is not None:
            self._attr_image_last_updated = snapshot.fetched_at
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self.coordinator.data
        attributes: dict[str, Any] = {
            "map_name": None,
            "map_age": None,
            "error": self.coordinator.last_error,
            "theme": self.coordinator.render_options.theme,
            "scale": self.coordinator.render_options.scale,
            "rotation": self.coordinator.render_options.rotate,
        }
        if snapshot is None:
            return attributes

        map_data = snapshot.map_data
        rendered = snapshot.rendered
        attributes.update(
            {
                "map_name": snapshot.map_name,
                "map_age": int((dt_util.utcnow() - snapshot.fetched_at).total_seconds()),
                "raw_size": snapshot.raw_size,
                "map_index": map_data.map_index,
                "map_sequence": map_data.map_sequence,
                "map_version": f"{map_data.major_version}.{map_data.minor_version}",
                "image_width": rendered.image.width,
                "image_height": rendered.image.height,
                "calibration_points": rendered.calibration_points(),
                "rooms": {str(number): room.as_dict() for number, room in map_data.rooms.items()},
                "vacuum_room": map_data.vacuum_room,
                "vacuum_position": map_data.vacuum_position.as_dict() if map_data.vacuum_position else None,
                "charger": map_data.charger.as_dict() if map_data.charger else None,
                "no_go_areas": [area.as_dict() for area in map_data.no_go_areas],
                "no_mopping_areas": [area.as_dict() for area in map_data.no_mopping_areas],
                "no_carpet_areas": [area.as_dict() for area in map_data.no_carpet_areas],
                "virtual_walls": [wall.as_dict() for wall in map_data.walls],
                "zones": [zone.as_dict() for zone in map_data.zones],
                "path": [point.as_dict() for point in _flatten_path(map_data.path)],
            }
        )
        return attributes

    @property
    def is_recording(self) -> bool:
        return False

    @property
    def frame_interval(self) -> float:
        return float(max(5, self.coordinator.scan_interval))


def _flatten_path(path) -> list:
    if path is None:
        return []
    return [point for chunk in path.chunks for point in chunk]
