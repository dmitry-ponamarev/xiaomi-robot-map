"""Tests for the map fetchers."""

from __future__ import annotations

import pytest

from custom_components.xiaomi_robot_map.cloud import XiaomiApiError
from custom_components.xiaomi_robot_map.provider import (
    MapFetcher,
    MapFetchError,
    MapPayload,
)
from tests.conftest import REAL_ASYNC_FETCH
from tests.fixtures.rrmap_builder import build_map

MAP_URL = "https://cdn.example.com/map.gz"


class FakeClient:
    """Minimal stand-in for :class:`XiaomiCloudClient`."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.object_names: list[str] = []
        self.downloads = 0

    async def async_get_map_url(self, device, map_name: str) -> str:
        self.object_names.append(map_name)
        if self.error is not None and len(self.object_names) == 1:
            raise self.error
        return MAP_URL

    async def async_download(self, url: str) -> bytes:
        self.downloads += 1
        assert url == MAP_URL
        return build_map()


def _fetcher(hass, client: FakeClient) -> MapFetcher:
    """Build a fetcher that uses the real (unpatched) fetch implementation."""
    return MapFetcher(
        hass,
        client,  # type: ignore[arg-type]
        host="192.168.1.50",
        token="0123456789abcdef0123456789abcdef",
        country="de",
        user_id="42",
        device_id="3123456789",
    )


@pytest.fixture(autouse=True)
def real_fetch(monkeypatch):
    """Undo the global stub so the real fetch logic is exercised."""
    monkeypatch.setattr(MapFetcher, "async_fetch", REAL_ASYNC_FETCH)


@pytest.fixture
def map_names(monkeypatch):
    """Return a callable that feeds map names to the fetcher."""
    names: list[str] = []

    def _use(*values: str):
        queue = list(values)

        def _query(host, token, retries=4):  # noqa: ARG001 - mirrors the real signature
            if not queue:
                raise AssertionError("the map name was queried more often than expected")
            return queue.pop(0)

        monkeypatch.setattr("custom_components.xiaomi_robot_map.provider.query_map_name", _query)
        names.append("patched")

    return _use


async def test_fetch_asks_the_robot_again_when_the_object_is_unknown(hass, map_names) -> None:
    """A cached map name goes stale: the cloud answers "objectname invalid"."""
    map_names("1695678900", "1695680000")
    client = FakeClient(error=XiaomiApiError("objectname invalid"))

    payload: MapPayload = await _fetcher(hass, client).async_fetch()

    assert client.object_names == ["1695678900", "1695680000"]
    assert payload.map_name == "1695680000"
    assert client.downloads == 1


async def test_fetch_does_not_retry_on_other_cloud_errors(hass, map_names) -> None:
    """Only the unknown-object answer triggers a second map name lookup."""
    map_names("1695678900")
    client = FakeClient(error=XiaomiApiError("internal error"))

    with pytest.raises(XiaomiApiError):
        await _fetcher(hass, client).async_fetch()

    assert client.object_names == ["1695678900"]
    assert client.downloads == 0


async def test_fetch_falls_back_when_the_configured_name_is_unknown(hass, map_names) -> None:
    """A hand written map name that the cloud rejects does not break the map."""
    map_names("robot-map")
    client = FakeClient(error=XiaomiApiError("objectname invalid"))
    fetcher = MapFetcher(
        hass,
        client,  # type: ignore[arg-type]
        host="192.168.1.50",
        token="0123456789abcdef0123456789abcdef",
        country="de",
        user_id="42",
        device_id="3123456789",
        map_name_override="manual-map",
    )

    payload = await fetcher.async_fetch()

    assert client.object_names == ["manual-map", "robot-map"]
    assert payload.map_name == "robot-map"


async def test_fetch_reports_a_missing_download_url(hass, map_names) -> None:
    """When the cloud hands out no URL the error names the map."""
    map_names("1695678900")

    class NoUrlClient(FakeClient):
        async def async_get_map_url(self, device, map_name: str) -> str | None:  # noqa: ARG002
            return None

    with pytest.raises(MapFetchError, match="1695678900"):
        await _fetcher(hass, NoUrlClient()).async_fetch()
