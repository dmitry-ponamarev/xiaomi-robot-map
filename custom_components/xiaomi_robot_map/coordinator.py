"""Data update coordinator: fetch, parse and render the map."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .cloud import XiaomiAuthError, XiaomiCloudClient, XiaomiCloudError
from .const import (
    CONF_IDLE_INTERVAL,
    CONF_LAYERS,
    CONF_ROTATE,
    CONF_SCALE,
    CONF_SCAN_INTERVAL,
    CONF_STORE_RAW_MAP,
    CONF_THEME,
    CONF_TRIM,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_LAYERS,
    DEFAULT_ROTATE,
    DEFAULT_SCALE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_THEME,
    DOMAIN,
    STORAGE_RAW_DIR,
)
from .provider import MapFetcher, MapPayload, parse_local_payload
from .render import RenderedMap, RenderOptions, render_map, render_placeholder
from .rr_map import RRMap, RRMapError
from .valetudo_map import ValetudoMapError

_LOGGER = logging.getLogger(__name__)

# Number of consecutive unchanged updates before switching to the idle interval.
IDLE_AFTER_UPDATES = 6

STATUS_TEXT = {
    "auth_failed": "AUTH FAILED",
    "fetch_failed": "MAP UNAVAILABLE",
    "parse_failed": "PARSE ERROR",
}


def build_render_options(options: dict) -> RenderOptions:
    """Create render options out of config entry options."""
    layers = options.get(CONF_LAYERS, DEFAULT_LAYERS)
    return RenderOptions(
        theme=options.get(CONF_THEME, DEFAULT_THEME),
        layers=frozenset(layers),
        scale=float(options.get(CONF_SCALE, DEFAULT_SCALE) or 1.0),
        rotate=int(options.get(CONF_ROTATE, DEFAULT_ROTATE) or 0),
        trim=_parse_trim(options.get(CONF_TRIM)),
    )


def _parse_trim(value: object) -> tuple[float, float, float, float]:
    default = (0.0, 0.0, 0.0, 0.0)
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 4:
            return default
        try:
            return tuple(float(item) for item in parts)  # type: ignore[return-value]
        except ValueError:
            return default
    if isinstance(value, dict):
        try:
            return (
                float(value.get("left", 0)),
                float(value.get("top", 0)),
                float(value.get("right", 0)),
                float(value.get("bottom", 0)),
            )
        except (TypeError, ValueError):
            return default
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return tuple(float(item) for item in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return default
    return default


@dataclass(slots=True)
class MapSnapshot:
    """Everything the entities need from one successful update."""

    map_data: RRMap
    rendered: RenderedMap
    map_name: str | None
    fetched_at: datetime
    raw_size: int
    png: bytes = field(default=b"")

    @property
    def position(self) -> tuple[int, int, int | None] | None:
        position = self.map_data.vacuum_position
        if position is None:
            return None
        return position.x, position.y, position.angle


class MapCoordinator(DataUpdateCoordinator[MapSnapshot]):
    """Poll the cloud, parse the blob and cache the rendered map."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: XiaomiCloudClient | None,
        fetcher: MapFetcher,
    ) -> None:
        self.entry = entry
        self.client = client
        self.fetcher = fetcher
        self.render_options = build_render_options({**entry.data, **entry.options})
        self.last_error: str | None = None
        self._static_updates = 0
        self._skip_until = 0.0
        self._last_position: tuple[int, int, int | None] | None = None
        self._last_map_name: str | None = None
        self._idle_interval = _option_int(entry, CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=_seconds(_option_int(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            config_entry=entry,
        )

    @property
    def scan_interval(self) -> int:
        return _option_int(self.entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    async def _async_update_data(self) -> MapSnapshot:
        now = time.monotonic()
        if self.data is not None and now < self._skip_until:
            _LOGGER.debug("Skipping map update: the robot is idle, next poll in %s s", int(self._skip_until - now))
            return self.data

        if self.client is not None and not self.client.logged_in:
            try:
                await self.client.async_login()
            except XiaomiAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except XiaomiCloudError as err:
                raise UpdateFailed(f"unable to log in to the Xiaomi cloud: {err}") from err

        try:
            payload = await self.fetcher.async_fetch()
        except XiaomiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except XiaomiCloudError as err:
            self.last_error = str(err)
            raise UpdateFailed(str(err)) from err

        snapshot = await self._async_build_snapshot(payload)
        self._track_activity(snapshot)
        return snapshot

    async def _async_build_snapshot(self, payload: MapPayload) -> MapSnapshot:
        try:
            map_data = await self.hass.async_add_executor_job(parse_local_payload, payload)
        except (RRMapError, ValetudoMapError) as err:
            self.last_error = str(err)
            raise UpdateFailed(f"unable to parse the map: {err}") from err
        except Exception as err:  # noqa: BLE001 - never kill the coordinator
            self.last_error = f"{type(err).__name__}: {err}"
            raise UpdateFailed(f"unexpected error while parsing the map: {err}") from err

        if map_data.is_empty:
            rendered = render_placeholder("EMPTY MAP", self.render_options)
        else:
            rendered = await self.hass.async_add_executor_job(
                render_map, map_data, self.render_options
            )
        self.last_error = None
        if self.entry.options.get(CONF_STORE_RAW_MAP, False):
            await self._async_store_raw(payload.raw)
        return MapSnapshot(
            map_data=map_data,
            rendered=rendered,
            map_name=payload.map_name,
            fetched_at=dt_util.utcnow(),
            raw_size=len(payload.raw),
            png=rendered.png,
        )

    async def _async_store_raw(self, raw: bytes) -> None:
        """Keep the last raw blob on disk for troubleshooting."""
        directory = self.hass.config.path(STORAGE_RAW_DIR)
        path = os.path.join(directory, f"{self.entry.entry_id}.bin")

        def _write() -> None:
            os.makedirs(directory, exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(raw)

        try:
            await self.hass.async_add_executor_job(_write)
        except OSError as err:
            _LOGGER.warning("Unable to store the raw map in %s: %s", path, err)

    def _track_activity(self, snapshot: MapSnapshot) -> None:
        position = snapshot.position
        changed = position != self._last_position or snapshot.map_name != self._last_map_name
        self._last_position = position
        self._last_map_name = snapshot.map_name
        if changed:
            self._static_updates = 0
            self._skip_until = 0.0
            return
        self._static_updates += 1
        if self._static_updates >= IDLE_AFTER_UPDATES:
            idle = max(self._idle_interval, self.scan_interval)
            self._skip_until = time.monotonic() + idle - self.scan_interval
            _LOGGER.debug("Robot idle, slowing map polling down to %s s", idle)

    async def async_force_refresh(self) -> None:
        """Ignore the idle back-off and refresh immediately."""
        self._skip_until = 0.0
        self._static_updates = 0
        self.fetcher.invalidate_cache()
        await self.async_request_refresh()

    def update_options(self, options: dict) -> None:
        """Apply new options without a reload."""
        self.render_options = build_render_options(options)
        self._idle_interval = int(options.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL) or DEFAULT_IDLE_INTERVAL)
        interval = int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL)
        self.update_interval = _seconds(interval)


def _option_int(entry: ConfigEntry, key: str, default: int) -> int:
    value = entry.options.get(key, entry.data.get(key, default))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _seconds(value: int):
    from datetime import timedelta

    return timedelta(seconds=max(5, value))
