"""Diagnostics support for the Xiaomi Robot Vacuum Map integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME, DOMAIN

TO_REDACT = {CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator
    snapshot = coordinator.data
    diagnostics: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_error": coordinator.last_error,
            "scan_interval": coordinator.scan_interval,
            "render": {
                "theme": coordinator.render_options.theme,
                "layers": sorted(coordinator.render_options.layers),
                "scale": coordinator.render_options.scale,
                "rotate": coordinator.render_options.rotate,
                "trim": coordinator.render_options.trim,
            },
        },
    }
    if snapshot is not None:
        diagnostics["map"] = {
            "map_name": snapshot.map_name,
            "fetched_at": snapshot.fetched_at.isoformat(),
            "raw_size": snapshot.raw_size,
            "image_size": [snapshot.rendered.image.width, snapshot.rendered.image.height],
            "rooms": len(snapshot.map_data.rooms),
            "path_points": snapshot.map_data.path.points_count if snapshot.map_data.path else 0,
            "warnings": snapshot.map_data.unknown_blocks,
        }
    else:
        diagnostics["map"] = None
    diagnostics["integration"] = DOMAIN
    return diagnostics
