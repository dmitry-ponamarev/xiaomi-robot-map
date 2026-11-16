"""Small authenticated HTTP view used to show a Xiaomi captcha in the UI.

Home Assistant cannot render an image inside a config-flow form, and the
``/local`` static route only exists when ``<config>/www`` was present at startup.
So the captcha image is kept in memory and served from an authenticated view:
the user opens the link shown in the form (already logged in to HA) and types the
characters into the form.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import get_url

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CAPTCHA_KEY = "captcha_images"
VIEW_PATH = f"/api/{DOMAIN}/captcha"


def _storage(hass: HomeAssistant) -> dict[str, tuple[bytes, str]]:
    data = hass.data.setdefault(DOMAIN, {})
    return data.setdefault(CAPTCHA_KEY, {})


MAX_STORED_CAPTCHAS = 20


def store_captcha(hass: HomeAssistant, flow_id: str, image: bytes) -> None:
    """Keep the newest captcha image for a flow."""
    content_type = "image/png" if image.startswith(b"\x89PNG") else "image/jpeg"
    storage = _storage(hass)
    storage[flow_id] = (image, content_type)
    while len(storage) > MAX_STORED_CAPTCHAS:
        storage.pop(next(iter(storage)))


def drop_captcha(hass: HomeAssistant, flow_id: str) -> None:
    """Forget the captcha of a finished flow."""
    _storage(hass).pop(flow_id, None)


CAPTCHA_URL_TTL = timedelta(minutes=30)


def captcha_url(hass: HomeAssistant, flow_id: str) -> str:
    """URL of the captcha image, signed so it can be used in an <img> tag."""
    path = f"{VIEW_PATH}/{flow_id}"
    try:
        path = async_sign_path(hass, path, CAPTCHA_URL_TTL)
    except Exception:  # noqa: BLE001 - signing needs hass.http, keep the plain path
        _LOGGER.debug("Unable to sign the captcha URL, using a plain path", exc_info=True)
    base = _base_url(hass)
    return f"{base}{path}" if base else path


def _base_url(hass: HomeAssistant) -> str:
    """Best absolute base URL of this instance (empty when none is configured)."""
    for getter in (
        lambda: get_url(hass, prefer_external=False),
        lambda: hass.config.internal_url,
        lambda: get_url(hass, prefer_external=True),
    ):
        try:
            value = getter()
        except Exception:  # noqa: BLE001 - NoURLAvailableError / NoExternalURL
            continue
        if value:
            return str(value).rstrip("/")
    return ""


class CaptchaView(HomeAssistantView):
    """Serve a captcha image to the signed-in user."""

    url = VIEW_PATH + "/{flow_id}"
    name = f"api:{DOMAIN}:captcha"
    requires_auth = True

    async def get(self, request: web.Request, flow_id: str) -> web.StreamResponse:
        """Return the captcha image of the given flow."""
        hass: HomeAssistant = request.app["hass"]
        entry = _storage(hass).get(flow_id)
        if entry is None:
            return web.Response(status=404, text="captcha not found")
        body, content_type = entry
        return web.Response(
            body=body,
            content_type=content_type,
            headers={"Cache-Control": "no-store"},
        )


def async_register_captcha_view(hass: HomeAssistant) -> None:
    """Register the view once per Home Assistant instance."""
    if hass.http is None:
        return
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("captcha_view_registered"):
        return
    hass.http.register_view(CaptchaView())
    data["captcha_view_registered"] = True
