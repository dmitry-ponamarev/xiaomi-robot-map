"""Config and options flow for the Xiaomi Robot Vacuum Map integration."""

from __future__ import annotations

import hashlib
import io
import logging
import re
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
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
from PIL import Image

from .cloud import (
    CloudDevice,
    XiaomiAuthError,
    XiaomiCaptchaRequired,
    XiaomiCloudClient,
    XiaomiCloudError,
    XiaomiTwoFactorRequired,
)
from .const import (
    AVAILABLE_COUNTRIES,
    CONF_BEARER_TOKEN,
    CONF_CAPTCHA_CODE,
    CONF_CLIENT_DEVICE_ID,
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_IDLE_INTERVAL,
    CONF_LAYERS,
    CONF_LOCAL_KIND,
    CONF_LOGIN_METHOD,
    CONF_MAP_NAME,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASS_TOKEN,
    CONF_PASSWORD,
    CONF_ROOM_NAMES,
    CONF_ROTATE,
    CONF_SCALE,
    CONF_SCAN_INTERVAL,
    CONF_SOURCE,
    CONF_STORE_RAW_MAP,
    CONF_THEME,
    CONF_TOKEN,
    CONF_TRIM,
    CONF_URL,
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
    LOCAL_KIND_RRMAP,
    LOCAL_KIND_VALETUDO,
    LOGIN_METHOD_PASSWORD,
    LOGIN_METHOD_TOKEN,
    LOGIN_METHODS,
    MAX_IDLE_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_IDLE_INTERVAL,
    MIN_SCAN_INTERVAL,
    SOURCE_CLOUD,
    SOURCE_LOCAL,
    THEMES,
)
from .http_views import (
    async_register_captcha_view,
    captcha_url,
    drop_captcha,
    store_captcha,
)
from .provider import LocalMapFetcher, MapFetchError, parse_local_payload
from .rr_map import RRMapError
from .valetudo_map import ValetudoMapError

_LOGGER = logging.getLogger(__name__)

CAPTCHA_SCALE = 4


def _enlarge_captcha(data: bytes) -> bytes:
    """Scale the tiny (125x42) Xiaomi captcha up so people can read it."""
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image = image.resize(
            (image.width * CAPTCHA_SCALE, image.height * CAPTCHA_SCALE), Image.LANCZOS
        )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    except (OSError, ValueError) as err:  # not an image after all
        _LOGGER.debug("Unable to enlarge the captcha: %s", err)
        return data
    return buffer.getvalue()


def _split_pass_token(raw: str, username: str) -> tuple[str, str]:
    """Accept ``userId:passToken`` as one value, or a bare passToken."""
    raw = raw.strip()
    if not raw:
        return "", ""
    user_id, separator, pass_token = raw.partition(":")
    if separator and pass_token.strip():
        return user_id.strip(), pass_token.strip()
    return username.strip(), raw


def _auth_detail(err: XiaomiAuthError) -> str:
    """Human readable technical detail shown under the login form."""
    return f"Xiaomi: {err.detail}" if err.detail else ""


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
        # Kept between the captcha form and its answer: the captcha is bound to
        # the cookies of the session that requested it.
        self._pending_client: XiaomiCloudClient | None = None
        self._client_device_id: str | None = None
        self._two_factor_url: str | None = None
        self._captcha_url: str | None = None

    # -- helpers ---------------------------------------------------------

    def _client(self, auth: dict[str, str] | None = None) -> XiaomiCloudClient:
        auth = auth or self._auth
        # Each attempt gets a private cookie jar, so a previous failed login can
        # never poison the next one.
        return XiaomiCloudClient(
            None,
            username=auth.get(CONF_USERNAME),
            password=auth.get(CONF_PASSWORD),
        )

    def _auth_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        return vol.Schema(
            {
                vol.Required(
                    CONF_LOGIN_METHOD,
                    default=defaults.get(CONF_LOGIN_METHOD, LOGIN_METHOD_PASSWORD),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(LOGIN_METHODS),
                        mode=SelectSelectorMode.LIST,
                        translation_key="login_method",
                    )
                ),
                vol.Optional(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
                ),
                vol.Optional(CONF_PASSWORD, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                ),
                vol.Optional(CONF_PASS_TOKEN, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_COUNTRY, default=defaults.get(CONF_COUNTRY, DEFAULT_COUNTRY)): SelectSelector(
                    SelectSelectorConfig(
                        options=[{"value": code, "label": COUNTRY_LABELS.get(code, code)} for code in AVAILABLE_COUNTRIES],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def _async_login_and_get_devices(
        self, auth: dict[str, str], captcha_code: str | None = None
    ) -> None:
        """Log in and load the device list, keeping the client alive on a captcha."""
        if self._pending_client is None:
            self._pending_client = self._client(auth)
        self._auth = auth
        client = self._pending_client
        try:
            if captcha_code is None and auth.get(CONF_PASS_TOKEN):
                await client.async_login_with_token(
                    auth.get(CONF_USER_ID, ""), auth[CONF_PASS_TOKEN]
                )
            elif captcha_code is None:
                await client.async_login()
            else:
                # answer the challenge that issued the image the user is looking
                # at: a new login would create a different captcha and the code
                # could never match
                await client.async_answer_captcha(captcha_code)
            devices = await client.async_get_devices([auth[CONF_COUNTRY]])
            self._client_device_id = client.device_id
        except XiaomiCaptchaRequired:
            raise
        except BaseException:
            await self._release_client()
            raise
        await self._release_client()
        self._devices = [device for device in devices if device.is_supported]

    async def _release_client(self) -> None:
        if self._pending_client is not None:
            await self._pending_client.async_close()
            self._pending_client = None

    # -- source selection ------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask whether the map comes from the cloud or from a local source."""
        if user_input is not None:
            if user_input.get(CONF_SOURCE) == SOURCE_LOCAL:
                return await self.async_step_local()
            return await self.async_step_cloud()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOURCE, default=SOURCE_CLOUD): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": SOURCE_CLOUD, "label": "Xiaomi cloud (Mi Home account)"},
                                {
                                    "value": SOURCE_LOCAL,
                                    "label": "Local, no cloud (Valetudo / local map URL)",
                                },
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    # -- cloud account ---------------------------------------------------

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the Xiaomi account credentials."""
        errors: dict[str, str] = {}
        detail = ""
        if user_input is not None:
            username = str(user_input.get(CONF_USERNAME) or "").strip()
            password = str(user_input.get(CONF_PASSWORD) or "")
            method = str(user_input.get(CONF_LOGIN_METHOD) or LOGIN_METHOD_PASSWORD)
            auth: dict[str, str] = {
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
                CONF_COUNTRY: str(user_input.get(CONF_COUNTRY) or DEFAULT_COUNTRY),
                CONF_LOGIN_METHOD: method,
            }
            if method == LOGIN_METHOD_TOKEN:
                user_id, pass_token = _split_pass_token(
                    str(user_input.get(CONF_PASS_TOKEN) or ""), username
                )
                if user_id and pass_token:
                    auth[CONF_USER_ID] = user_id
                    auth[CONF_PASS_TOKEN] = pass_token
                else:
                    errors["base"] = "invalid_format"
                    detail = (
                        "Нужна пара `userId:passToken` из cookies account.xiaomi.com "
                        "(DevTools → Application → Cookies)."
                    )
            elif not username or not password:
                errors["base"] = "invalid_auth"
                detail = "Укажите логин и пароль либо выберите вход по токену."
            if errors:
                return self.async_show_form(
                    step_id="cloud",
                    data_schema=self._auth_schema(user_input),
                    errors=errors,
                    description_placeholders={"detail": detail},
                )
            try:
                await self._async_login_and_get_devices(auth)
            except XiaomiCaptchaRequired as err:
                _LOGGER.debug("Xiaomi requires a captcha for this login")
                self._captcha_url = err.url
                return await self.async_step_captcha()
            except XiaomiTwoFactorRequired as err:
                _LOGGER.debug("Xiaomi requires 2FA: %s", err)
                self._two_factor_url = err.url
                return await self.async_step_two_factor()
            except XiaomiAuthError as err:
                _LOGGER.debug("Xiaomi login failed: %s", err)
                errors["base"] = "invalid_auth"
                detail = _auth_detail(err)
            except XiaomiCloudError as err:
                _LOGGER.debug("Xiaomi cloud is unreachable: %s", err)
                errors["base"] = "cannot_connect"
                detail = str(err)
            else:
                return await self.async_step_device()

        return self.async_show_form(
            step_id="cloud",
            data_schema=self._auth_schema(user_input),
            errors=errors,
            description_placeholders={"detail": detail},
        )

    # -- captcha ---------------------------------------------------------

    async def async_step_captcha(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Link to the captcha image and submit the typed code."""
        if self._pending_client is None:
            return await self.async_step_cloud()

        if user_input is None:
            return await self._async_show_captcha({})

        # spaces and dashes are noise people add while copying a distorted code
        code = "".join(char for char in str(user_input.get(CONF_CAPTCHA_CODE) or "") if char.isalnum())
        if not code:
            # the empty field is the documented "give me another captcha" action
            return await self._async_new_challenge({})

        for variant in dict.fromkeys((code, code.upper(), code.lower())):
            try:
                await self._async_login_and_get_devices(self._auth, captcha_code=variant)
            except XiaomiCaptchaRequired as err:
                _LOGGER.debug("Captcha answer %r was rejected by Xiaomi", variant)
                self._captcha_url = err.url
                continue
            except XiaomiTwoFactorRequired as err:
                self._two_factor_url = err.url
                return await self.async_step_two_factor()
            except XiaomiAuthError as err:
                await self._release_client()
                return self.async_show_form(
                    step_id="cloud",
                    data_schema=self._auth_schema(None),
                    errors={"base": "invalid_auth"},
                    description_placeholders={"detail": _auth_detail(err)},
                )
            except XiaomiCloudError as err:
                return await self._async_show_captcha({"base": "cannot_connect"}, str(err))
            else:
                return await self.async_step_device()

        return await self._async_new_challenge({"base": "invalid_captcha"})

    async def _async_new_challenge(self, errors: dict[str, str]) -> ConfigFlowResult:
        """Start a fresh login so Xiaomi issues a new captcha."""
        await self._release_client()
        try:
            await self._async_login_and_get_devices(self._auth)
        except XiaomiCaptchaRequired as err:
            self._captcha_url = err.url
        except XiaomiTwoFactorRequired as err:
            self._two_factor_url = err.url
            return await self.async_step_two_factor()
        except XiaomiAuthError as err:
            await self._release_client()
            return self.async_show_form(
                step_id="cloud",
                data_schema=self._auth_schema(None),
                errors={"base": "invalid_auth"},
                description_placeholders={"detail": _auth_detail(err)},
            )
        except XiaomiCloudError as err:
            return await self._async_show_captcha({"base": "cannot_connect"}, str(err))
        else:
            return await self.async_step_device()
        return await self._async_show_captcha(errors)

    async def _async_show_captcha(self, errors: dict[str, str], detail: str = "") -> ConfigFlowResult:
        """Render the captcha step with a link to a readable image."""
        image_url, image_error = await self._async_store_captcha()
        if image_error:
            detail = f"{detail}\n{image_error}".strip()
        if not image_url:
            detail = f"{detail}\nАккаунт Xiaomi требует капчу, но Home Assistant не смог подготовить ссылку.".strip()
        return self.async_show_form(
            step_id="captcha",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_CAPTCHA_CODE, default=""): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "image_url": image_url,
                "detail": detail,
                # a stale translation (cached by the browser) still references this
                # key; an empty value keeps the form from failing with MISSING_VALUE
                "image_markdown": "",
            },
        )

    # -- two factor ------------------------------------------------------

    async def async_step_two_factor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask the user to confirm the login in a browser, then retry."""
        errors: dict[str, str] = {}
        detail = ""
        if user_input is not None:
            try:
                await self._async_login_and_get_devices(self._auth)
            except XiaomiCaptchaRequired as err:
                self._captcha_url = err.url
                return await self.async_step_captcha()
            except XiaomiTwoFactorRequired as err:
                self._two_factor_url = err.url
                errors["base"] = "two_factor"
            except XiaomiAuthError as err:
                errors["base"] = "invalid_auth"
                detail = _auth_detail(err)
            except XiaomiCloudError as err:
                errors["base"] = "cannot_connect"
                detail = str(err)
            else:
                return await self.async_step_device()

        return self.async_show_form(
            step_id="two_factor",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"url": self._two_factor_url or "", "detail": detail},
        )

    async def _async_store_captcha(self) -> tuple[str, str]:
        """Fetch the captcha, enlarge it for readability, return its link."""
        client = self._pending_client
        url = self._captcha_url or (client.auth_url if client is not None else None)
        if client is None or not url:
            return "", "Не удалось определить адрес капчи — попробуйте войти заново."
        try:
            data = await client.async_fetch_captcha(url)
        except XiaomiCloudError as err:
            _LOGGER.warning("Unable to download the Xiaomi captcha: %s", err)
            return "", f"Не удалось загрузить картинку капчи: {err}"

        enlarged = await self.hass.async_add_executor_job(_enlarge_captcha, data)
        async_register_captcha_view(self.hass)
        store_captcha(self.hass, self.flow_id, enlarged)
        return captcha_url(self.hass, self.flow_id), ""

    # -- local source ----------------------------------------------------

    async def async_step_local(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Configure a map source that does not need the Xiaomi cloud."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = str(user_input.get(CONF_URL) or "").strip()
            kind = str(user_input.get(CONF_LOCAL_KIND) or LOCAL_KIND_VALETUDO)
            username = str(user_input.get(CONF_USERNAME) or "").strip() or None
            password = str(user_input.get(CONF_PASSWORD) or "") or None
            token = str(user_input.get(CONF_BEARER_TOKEN) or "").strip() or None
            name = str(user_input.get(CONF_NAME) or "").strip()

            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                errors[CONF_URL] = "invalid_url"
            else:
                fetcher = LocalMapFetcher(
                    self.hass,
                    url=url,
                    kind=kind,
                    username=username,
                    password=password,
                    token=token,
                )
                try:
                    payload = await fetcher.async_fetch()
                    await self.hass.async_add_executor_job(parse_local_payload, payload)
                except MapFetchError as err:
                    _LOGGER.debug("The local map source is unreachable: %s", err)
                    errors["base"] = "cannot_connect"
                except (RRMapError, ValetudoMapError) as err:
                    _LOGGER.debug("The local map source returned an unexpected document: %s", err)
                    errors["base"] = "invalid_format"
                else:
                    await self.async_set_unique_id(hashlib.sha256(url.encode()).hexdigest()[:16])
                    self._abort_if_unique_id_configured()
                    title = name or parsed.hostname or "Local map"
                    return self.async_create_entry(
                        title=title,
                        data={
                            CONF_SOURCE: SOURCE_LOCAL,
                            CONF_LOCAL_KIND: kind,
                            CONF_URL: url,
                            CONF_USERNAME: username or "",
                            CONF_PASSWORD: password or "",
                            CONF_BEARER_TOKEN: token or "",
                            CONF_NAME: title,
                            CONF_MODEL: "Valetudo" if kind == LOCAL_KIND_VALETUDO else "Local RRMap",
                        },
                    )

        return self.async_show_form(step_id="local", data_schema=self._local_schema(user_input), errors=errors)

    def _local_schema(self, defaults: dict[str, Any] | None = None) -> vol.Schema:
        defaults = defaults or {}
        return vol.Schema(
            {
                vol.Required(
                    CONF_LOCAL_KIND, default=defaults.get(CONF_LOCAL_KIND, LOCAL_KIND_VALETUDO)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {
                                "value": LOCAL_KIND_VALETUDO,
                                "label": "Valetudo — JSON from /api/v2/robot/state/map",
                            },
                            {"value": LOCAL_KIND_RRMAP, "label": "Raw RRMap file over HTTP (.gz / .bin)"},
                        ],
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Optional(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
                ),
                vol.Optional(CONF_PASSWORD, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                ),
                vol.Optional(CONF_BEARER_TOKEN, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
            }
        )

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
                drop_captcha(self.hass, self.flow_id)
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
                        CONF_SOURCE: SOURCE_CLOUD,
                        CONF_CLIENT_DEVICE_ID: self._client_device_id or "",
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
                except XiaomiCaptchaRequired:
                    errors["base"] = "captcha_required"
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
                                CONF_SOURCE: SOURCE_CLOUD,
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
        countries = [self._auth[CONF_COUNTRY]] + [c for c in AVAILABLE_COUNTRIES if c != self._auth[CONF_COUNTRY]]
        try:
            await client.async_login()
            for country in countries:
                for device in await client.async_get_devices([country]):
                    if device.token.lower() == token and device.is_supported:
                        return device
        finally:
            await client.async_close()
        return None

    # -- reauth ----------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start the reauthentication flow."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for new credentials."""
        errors: dict[str, str] = {}
        detail = ""
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
            except XiaomiCaptchaRequired as err:
                errors["base"] = "captcha_required"
                detail = _auth_detail(err)
            except XiaomiAuthError as err:
                errors["base"] = "invalid_auth"
                detail = _auth_detail(err)
            except XiaomiCloudError as err:
                errors["base"] = "cannot_connect"
                detail = str(err)
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
            description_placeholders={"detail": detail},
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
