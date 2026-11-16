"""Config and options flow for the Xiaomi Robot Vacuum Map integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .cloud import CloudDevice, XiaomiAuthError, XiaomiCloudClient, XiaomiCloudError
from .const import (
    AVAILABLE_COUNTRIES,
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_IDLE_INTERVAL,
    CONF_LAYERS,
    CONF_MAP_NAME,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_ROOM_NAMES,
    CONF_ROTATE,
    CONF_SCALE,
    CONF_SCAN_INTERVAL,
    CONF_STORE_RAW_MAP,
    CONF_THEME,
    CONF_TOKEN,
    CONF_TRIM,
    CONF_USER_ID,
    CONF_USERNAME,
    DEFAULT_COUNTRY,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_LAYERS,
    DEFAULT_ROTATE,
    DEFAULT_SCALE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_THEME,
    DOMAIN,
    MAX_IDLE_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_IDLE_INTERVAL,
    MIN_SCAN_INTERVAL,
    THEMES,
)

_LOGGER = logging.getLogger(__name__)

MANUAL_DEVICE = "__manual__"
TOKEN_RE = re.compile(r"^[0-9a-fA-F]{32}$")

COUNTRY_LABELS = {
    "cn": "China (cn)",
    "de": "Germany (de)",
    "us": "United States (us)",
    "ru": "Russia (ru)",
    "tw": "Taiwan (tw)",
    "sg": "Singapore (sg)",
    "in": "India (in)",
    "i2": "International (i2)",
}

LAYER_LABELS = {
    "robot": "Robot position",
    "charger": "Charger",
    "path": "Cleaned path",
    "goto_path": "Go-to path",
    "predicted_path": "Predicted path",
    "virtual_walls": "Virtual walls",
    "zones": "Cleaned zones",
    "no_go_areas": "No-go areas",
    "no_mopping_areas": "No-mopping areas",
    "no_carpet_areas": "No-carpet areas",
    "obstacles": "Obstacles",
    "room_numbers": "Room numbers",
}


class XiaomiRobotMapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of a vacuum."""

    VERSION = 1

    def __init__(self) -> None:
        self._auth: dict[str, str] = {}
        self._devices: list[CloudDevice] = []
        self._reauth_entry: ConfigEntry | None = None

    # -- helpers ---------------------------------------------------------

    def _client(self, auth: dict[str, str] | None = None) -> XiaomiCloudClient:
        auth = auth or self._auth
        return XiaomiCloudClient(
            async_get_clientsession(self.hass),
            username=auth.get(CONF_USERNAME),
            password=auth.get(CONF_PASSWORD),
        )

    def _auth_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        return vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
                ),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                ),
                vol.Required(CONF_COUNTRY, default=defaults.get(CONF_COUNTRY, DEFAULT_COUNTRY)): SelectSelector(
                    SelectSelectorConfig(
                        options=[{"value": code, "label": COUNTRY_LABELS.get(code, code)} for code in AVAILABLE_COUNTRIES],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def _async_login_and_get_devices(self, auth: dict[str, str]) -> None:
        client = self._client(auth)
        await client.async_login()
        devices = await client.async_get_devices([auth[CONF_COUNTRY]])
        self._auth = auth
        self._devices = [device for device in devices if device.is_supported]

    # -- cloud account ---------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the Xiaomi account credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            auth = {
                CONF_USERNAME: str(user_input[CONF_USERNAME]).strip(),
                CONF_PASSWORD: str(user_input[CONF_PASSWORD]),
                CONF_COUNTRY: str(user_input[CONF_COUNTRY]),
            }
            try:
                await self._async_login_and_get_devices(auth)
            except XiaomiAuthError as err:
                _LOGGER.debug("Xiaomi login failed: %s", err)
                errors["base"] = "two_factor" if "additional authentication" in str(err) else "invalid_auth"
            except XiaomiCloudError as err:
                _LOGGER.debug("Xiaomi cloud is unreachable: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_device()

        return self.async_show_form(step_id="user", data_schema=self._auth_schema(user_input), errors=errors)

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the vacuum and its LAN address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = str(user_input[CONF_DEVICE_ID])
            if device_id == MANUAL_DEVICE:
                return await self.async_step_manual()
            device = next((item for item in self._devices if item.device_id == device_id), None)
            if device is None:
                errors["base"] = "device_not_found"
            else:
                await self.async_set_unique_id(device.device_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                return self.async_create_entry(
                    title=device.name,
                    data={
                        **self._auth,
                        CONF_DEVICE_ID: device.device_id,
                        CONF_MODEL: device.model,
                        CONF_NAME: device.name,
                        CONF_TOKEN: device.token,
                        CONF_HOST: str(user_input[CONF_HOST]).strip(),
                        CONF_USER_ID: device.user_id or "",
                    },
                )

        return self.async_show_form(step_id="device", data_schema=self._device_schema(user_input), errors=errors)

    def _device_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        options = [{"value": device.device_id, "label": f"{device.name} ({device.model})"} for device in self._devices]
        options.append({"value": MANUAL_DEVICE, "label": "Enter host and token manually"})
        default = (defaults or {}).get(CONF_DEVICE_ID, options[0]["value"])
        return vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID, default=default): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                ),
                vol.Required(CONF_HOST, default=(defaults or {}).get(CONF_HOST, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
            }
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Fallback: resolve a device from a host/token pair."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = str(user_input[CONF_TOKEN]).strip().lower()
            host = str(user_input[CONF_HOST]).strip()
            if not TOKEN_RE.match(token):
                errors[CONF_TOKEN] = "invalid_token"
            elif not host:
                errors[CONF_HOST] = "invalid_host"
            else:
                try:
                    device = await self._async_resolve_device(token)
                except XiaomiCloudError:
                    errors["base"] = "cannot_connect"
                else:
                    if device is None:
                        errors["base"] = "device_not_found"
                    else:
                        await self.async_set_unique_id(device.device_id)
                        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                        return self.async_create_entry(
                            title=user_input.get(CONF_NAME) or device.name,
                            data={
                                **self._auth,
                                CONF_DEVICE_ID: device.device_id,
                                CONF_MODEL: device.model,
                                CONF_NAME: user_input.get(CONF_NAME) or device.name,
                                CONF_TOKEN: token,
                                CONF_HOST: host,
                                CONF_USER_ID: device.user_id or "",
                            },
                        )

        return self.async_show_form(step_id="manual", data_schema=self._manual_schema(user_input), errors=errors)

    def _manual_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
            }
        )

    async def _async_resolve_device(self, token: str) -> CloudDevice | None:
        """Find a device by token, searching all known countries if needed."""
        client = self._client()
        await client.async_login()
        countries = [self._auth[CONF_COUNTRY]] + [c for c in AVAILABLE_COUNTRIES if c != self._auth[CONF_COUNTRY]]
        for country in countries:
            for device in await client.async_get_devices([country]):
                if device.token.lower() == token and device.is_supported:
                    return device
        return None

    # -- reauth ----------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start the reauthentication flow."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for new credentials."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        defaults = dict(entry.data) if entry else {}
        if user_input is not None:
            auth = {
                CONF_USERNAME: str(user_input[CONF_USERNAME]).strip(),
                CONF_PASSWORD: str(user_input[CONF_PASSWORD]),
                CONF_COUNTRY: str(user_input[CONF_COUNTRY]),
            }
            try:
                await self._async_login_and_get_devices(auth)
            except XiaomiAuthError:
                errors["base"] = "invalid_auth"
            except XiaomiCloudError:
                errors["base"] = "cannot_connect"
            else:
                if entry is None:
                    return self.async_abort(reason="reauth_failed")
                device = next(
                    (item for item in self._devices if item.device_id == entry.data.get(CONF_DEVICE_ID)),
                    None,
                )
                new_data = {**entry.data, **auth}
                if device is not None:
                    new_data.update({CONF_TOKEN: device.token, CONF_MODEL: device.model})
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self._auth_schema({**defaults, **(user_input or {})}),
            errors=errors,
        )

    # -- options ---------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return XiaomiRobotMapOptionsFlow(config_entry)


class XiaomiRobotMapOptionsFlow(OptionsFlow):
    """Options: polling, rendering and diagnostics."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            options = dict(user_input)
            options[CONF_ROOM_NAMES] = _parse_room_names(options.get(CONF_ROOM_NAMES, ""))
            options[CONF_TRIM] = _normalise_trim(options.get(CONF_TRIM, ""))
            return self.async_create_entry(title="", data=options)

        current = {**self._entry.data, **self._entry.options}
        layers = current.get(CONF_LAYERS) or DEFAULT_LAYERS
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=int(current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_IDLE_INTERVAL, default=int(current.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL))
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_IDLE_INTERVAL,
                        max=MAX_IDLE_INTERVAL,
                        step=10,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(CONF_THEME, default=current.get(CONF_THEME, DEFAULT_THEME)): SelectSelector(
                    SelectSelectorConfig(options=THEMES, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_LAYERS, default=list(layers)): cv.multi_select(LAYER_LABELS),
                vol.Optional(CONF_ROTATE, default=str(current.get(CONF_ROTATE, DEFAULT_ROTATE))): SelectSelector(
                    SelectSelectorConfig(options=["0", "90", "180", "270"], mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_SCALE, default=str(current.get(CONF_SCALE, DEFAULT_SCALE))): SelectSelector(
                    SelectSelectorConfig(options=["1.0", "1.5", "2.0", "3.0"], mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_TRIM, default=_trim_to_str(current.get(CONF_TRIM))): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Optional(CONF_MAP_NAME, default=current.get(CONF_MAP_NAME, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Optional(
                    CONF_ROOM_NAMES, default=_room_names_to_str(current.get(CONF_ROOM_NAMES))
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Optional(
                    CONF_STORE_RAW_MAP, default=bool(current.get(CONF_STORE_RAW_MAP, False))
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _parse_room_names(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    if not isinstance(value, str):
        return {}
    result: dict[str, str] = {}
    for part in re.split(r"[,;\n]", value):
        if ":" not in part:
            continue
        key, _, name = part.partition(":")
        key, name = key.strip(), name.strip()
        if key.isdigit() and name:
            result[key] = name
    return result


def _room_names_to_str(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return ", ".join(f"{key}: {name}" for key, name in value.items())


def _normalise_trim(value: Any) -> str:
    try:
        parts = [part.strip() for part in str(value).split(",")]
        numbers = [max(0.0, min(90.0, float(part))) for part in parts if part != ""]
    except ValueError:
        return "0,0,0,0"
    if len(numbers) != 4:
        return "0,0,0,0"
    return ",".join(str(int(number)) for number in numbers)


def _trim_to_str(value: Any) -> str:
    if isinstance(value, str) and value.count(",") == 3:
        return value
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return ",".join(str(int(float(item))) for item in value)
    if isinstance(value, dict):
        return ",".join(
            str(int(float(value.get(key, 0)))) for key in ("left", "top", "right", "bottom")
        )
    return "0,0,0,0"
