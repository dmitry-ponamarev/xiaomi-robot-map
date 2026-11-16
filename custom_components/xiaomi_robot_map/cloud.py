"""Async client for the Xiaomi cloud API (login, devices, map download).

The protocol is the one used by the official Mi Home app:

* ``account.xiaomi.com`` for the web login (the result is a ``serviceToken``
  cookie scoped to ``*.api.io.mi.com``),
* RC4 encrypted API calls signed with HMAC-SHA1 over the request parameters,
* a plain signed URL for the map blob itself.

Only the endpoints needed to fetch a RRMap are implemented.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any

import aiohttp
from Crypto.Cipher import ARC4

from .const import AVAILABLE_COUNTRIES

_LOGGER = logging.getLogger(__name__)

LOGIN_URL = "https://account.xiaomi.com/pass/serviceLogin"
LOGIN_AUTH_URL = "https://account.xiaomi.com/pass/serviceLoginAuth2"
TIMEOUT = aiohttp.ClientTimeout(total=30)
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=60, sock_read=30)

MAP_URL_ENDPOINT = "/home/getmapfileurl"
MAP_URL_ENDPOINT_V2 = "/v2/home/get_interim_file_url"
DEVICE_LIST_ENDPOINT = "/home/device_list"
HOMES_ENDPOINT = "/v2/homeroom/gethome"
HOME_DEVICES_ENDPOINT = "/v2/home/home_device_list"


class XiaomiCloudError(Exception):
    """Base error for cloud communication."""


class XiaomiAuthError(XiaomiCloudError):
    """Raised when the account credentials are rejected."""


class XiaomiTwoFactorRequired(XiaomiAuthError):
    """Raised when Xiaomi asks for an extra verification step."""

    def __init__(self, url: str) -> None:
        super().__init__(f"additional authentication required: {url}")
        self.url = url


class XiaomiApiError(XiaomiCloudError):
    """Raised when the cloud answers with an unexpected payload."""


@dataclass(slots=True, frozen=True)
class CloudDevice:
    """A device as reported by the cloud."""

    device_id: str
    name: str
    model: str
    token: str
    country: str
    home_id: int | None = None
    user_id: str | None = None

    @property
    def is_supported(self) -> bool:
        from .const import SUPPORTED_MODEL_PREFIXES

        return self.model.startswith(SUPPORTED_MODEL_PREFIXES)


@dataclass(slots=True, frozen=True)
class CloudHome:
    """A Xiaomi home (used for home sharing accounts)."""

    home_id: int
    owner_id: int


class XiaomiCloudClient:
    """Minimal Xiaomi cloud client tailored to vacuum map extraction."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._agent = _generate_agent()
        self._device_id = _generate_device_id()
        self._sign: str | None = None
        self._ssecurity: str | None = None
        self._user_id: str | None = None
        self._c_user_id: str | None = None
        self._pass_token: str | None = None
        self._location: str | None = None
        self._service_token: str | None = None
        self.auth_url: str | None = None
        if hasattr(self._session, "cookie_jar"):
            self._session.cookie_jar.update_cookies(
                {
                    "sdkVersion": "accountsdk-18.8.15",
                    "deviceId": self._device_id,
                }
            )

    # -- properties ------------------------------------------------------

    @property
    def logged_in(self) -> bool:
        return self._service_token is not None

    @property
    def user_id(self) -> str | None:
        return self._user_id

    # -- login -----------------------------------------------------------

    async def async_login(self) -> None:
        """Log in and obtain a service token."""
        self.auth_url = None
        self._sign = await self._async_login_step_1()
        await self._async_login_step_2()
        await self._async_login_step_3()

    async def _async_login_step_1(self) -> str:
        url = f"{LOGIN_URL}?sid=xiaomiio&_json=true"
        headers = {"User-Agent": self._agent}
        try:
            async with self._session.get(
                url, headers=headers, cookies={"userId": self._username or ""}, timeout=TIMEOUT
            ) as response:
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise XiaomiCloudError(f"unable to reach account.xiaomi.com: {err}") from err
        payload = _to_json(text)
        sign = payload.get("_sign") if isinstance(payload, dict) else None
        if not sign:
            raise XiaomiAuthError("login step 1 failed: no _sign in the response")
        return sign

    async def _async_login_step_2(self) -> None:
        headers = {
            "User-Agent": self._agent,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        params = {
            "sid": "xiaomiio",
            "hash": hashlib.md5((self._password or "").encode()).hexdigest().upper(),
            "callback": "https://sts.api.io.mi.com/sts",
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "user": self._username or "",
            "_sign": self._sign or "",
            "_json": "true",
        }
        try:
            async with self._session.post(
                LOGIN_AUTH_URL, headers=headers, params=params, timeout=TIMEOUT
            ) as response:
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise XiaomiCloudError(f"unable to reach account.xiaomi.com: {err}") from err

        payload = _to_json(text)
        if not isinstance(payload, dict):
            raise XiaomiAuthError("login step 2 failed: unexpected response")
        if not payload.get("ssecurity"):
            if url := payload.get("notificationUrl"):
                self.auth_url = url
                raise XiaomiTwoFactorRequired(url)
            description = payload.get("desc") or payload.get("description") or "invalid credentials"
            raise XiaomiAuthError(str(description))

        self._ssecurity = str(payload["ssecurity"])
        self._user_id = str(payload.get("userId"))
        self._c_user_id = str(payload.get("cUserId"))
        self._pass_token = payload.get("passToken")
        self._location = payload.get("location")
        if not self._location:
            raise XiaomiAuthError("login step 2 failed: no redirect location")

    async def _async_login_step_3(self) -> None:
        headers = {"User-Agent": self._agent}
        try:
            async with self._session.get(self._location or "", headers=headers, timeout=TIMEOUT) as response:
                await response.read()
                token = response.cookies.get("serviceToken")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise XiaomiCloudError(f"unable to obtain a service token: {err}") from err
        if not token:
            raise XiaomiAuthError("login step 3 failed: no serviceToken cookie")
        self._service_token = token.value

    # -- API -------------------------------------------------------------

    async def async_get_devices(self, countries: list[str] | None = None) -> list[CloudDevice]:
        """Return all devices of the account, optionally limited to country hosts."""
        devices: list[CloudDevice] = []
        seen: set[str] = set()
        for country in countries or AVAILABLE_COUNTRIES:
            for device in await self._async_devices_for_country(country):
                if device.device_id in seen or not device.token:
                    continue
                seen.add(device.device_id)
                devices.append(device)
        return devices

    async def _async_devices_for_country(self, country: str) -> list[CloudDevice]:
        payload = await self._async_api_call(
            country,
            DEVICE_LIST_ENDPOINT,
            {"data": '{"getVirtualModel":false,"getHuamiDevices":0}'},
        )
        if not payload:
            return []
        result = payload.get("result") or {}
        raw_devices = result.get("list") or []
        if raw_devices:
            return [
                CloudDevice(
                    device_id=str(device.get("did")),
                    name=str(device.get("name") or device.get("model")),
                    model=str(device.get("model")),
                    token=str(device.get("token")),
                    country=country,
                    user_id=str(self._user_id) if self._user_id else None,
                )
                for device in raw_devices
            ]
        return await self._async_devices_from_homes(country)

    async def _async_devices_from_homes(self, country: str) -> list[CloudDevice]:
        """Fallback for accounts that only see their devices through homes."""
        payload = await self._async_api_call(
            country,
            HOMES_ENDPOINT,
            {
                "data": json.dumps(
                    {"fg": True, "fetch_share": True, "fetch_share_dev": True, "limit": 300, "app_ver": 7}
                )
            },
        )
        if not payload:
            return []
        result = payload.get("result") or {}
        homes = [
            CloudHome(int(home["id"]), int(home["uid"]))
            for home in (result.get("homelist") or []) + (result.get("share_home_list") or [])
            if home.get("id") and home.get("uid") is not None
        ]
        devices: list[CloudDevice] = []
        for home in homes:
            home_payload = await self._async_api_call(
                country,
                HOME_DEVICES_ENDPOINT,
                {
                    "data": json.dumps(
                        {
                            "home_id": home.home_id,
                            "home_owner": home.owner_id,
                            "limit": 200,
                            "get_split_device": True,
                            "support_smart_home": True,
                        }
                    )
                },
            )
            if not home_payload:
                continue
            for device in (home_payload.get("result") or {}).get("device_info") or []:
                devices.append(
                    CloudDevice(
                        device_id=str(device.get("did")),
                        name=str(device.get("name") or device.get("model")),
                        model=str(device.get("model")),
                        token=str(device.get("token")),
                        country=country,
                        home_id=home.home_id,
                        user_id=str(home.owner_id),
                    )
                )
        return devices

    async def async_get_map_url(self, device: CloudDevice, map_name: str) -> str | None:
        """Ask the cloud for a temporary download URL of a map object."""
        payload = await self._async_api_call(
            device.country,
            MAP_URL_ENDPOINT,
            {"data": json.dumps({"obj_name": map_name})},
        )
        url = ((payload or {}).get("result") or {}).get("url")
        if url:
            return str(url)
        # Some firmwares only expose the interim object storage endpoint.
        owner = device.user_id or self._user_id or ""
        payload = await self._async_api_call(
            device.country,
            MAP_URL_ENDPOINT_V2,
            {"data": json.dumps({"obj_name": f"{owner}/{device.device_id}/{map_name}"})},
        )
        url = ((payload or {}).get("result") or {}).get("url")
        return str(url) if url else None

    async def async_download(self, url: str) -> bytes:
        """Download the raw map blob."""
        try:
            async with self._session.get(url, timeout=DOWNLOAD_TIMEOUT) as response:
                if response.status != 200:
                    raise XiaomiApiError(f"map download failed with HTTP {response.status}")
                data = await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise XiaomiCloudError(f"unable to download the map: {err}") from err
        if not data:
            raise XiaomiApiError("the map download returned an empty body")
        return data

    async def _async_api_call(self, country: str, endpoint: str, params: dict[str, str]) -> dict[str, Any] | None:
        if not self.logged_in:
            raise XiaomiAuthError("not logged in")
        url = _api_url(country) + endpoint
        millis = round(time.time() * 1000)
        nonce = _generate_nonce(millis)
        signed_nonce = self._signed_nonce(nonce)
        fields = self._encrypt_params(url, "POST", signed_nonce, nonce, dict(params))

        headers = {
            "Accept-Encoding": "identity",
            "User-Agent": self._agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
        }
        cookies = {
            "userId": str(self._user_id),
            "yetAnotherServiceToken": str(self._service_token),
            "serviceToken": str(self._service_token),
            "locale": "en_GB",
            "timezone": "GMT+02:00",
            "is_daylight": "1",
            "dst_offset": "3600000",
            "channel": "MI_APP_STORE",
        }
        try:
            async with self._session.post(url, headers=headers, cookies=cookies, params=fields, timeout=TIMEOUT) as response:
                if response.status in (401, 403):
                    self._service_token = None
                    raise XiaomiAuthError(f"cloud rejected the request (HTTP {response.status})")
                if response.status != 200:
                    raise XiaomiApiError(f"cloud request failed with HTTP {response.status}")
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise XiaomiCloudError(f"cloud request failed: {err}") from err

        decoded = _decrypt_rc4(self._signed_nonce(fields["_nonce"]), text)
        try:
            payload = json.loads(decoded)
        except ValueError as err:
            raise XiaomiApiError(f"unable to decode the cloud response: {err}") from err
        if isinstance(payload, dict) and payload.get("code") not in (0, None):
            message = payload.get("message") or payload.get("msg") or payload.get("code")
            if payload.get("code") in (2, 3, 4, 5):
                self._service_token = None
                raise XiaomiAuthError(str(message))
            raise XiaomiApiError(str(message))
        return payload if isinstance(payload, dict) else None

    # -- crypto helpers ---------------------------------------------------

    def _signed_nonce(self, nonce: str) -> str:
        digest = hashlib.sha256(base64.b64decode(self._ssecurity) + base64.b64decode(nonce))
        return base64.b64encode(digest.digest()).decode()

    def _encrypt_params(self, url: str, method: str, signed_nonce: str, nonce: str, params: dict[str, str]) -> dict[str, str]:
        params["rc4_hash__"] = _enc_signature(url, method, signed_nonce, params)
        for key, value in params.items():
            params[key] = _encrypt_rc4(signed_nonce, value)
        params.update(
            {
                "signature": _enc_signature(url, method, signed_nonce, params),
                "ssecurity": self._ssecurity or "",
                "_nonce": nonce,
            }
        )
        return params


# --------------------------------------------------------------------------
# Module level helpers (also covered by tests)
# --------------------------------------------------------------------------


def _api_url(country: str) -> str:
    return "https://" + ("" if country == "cn" else f"{country}.") + "api.io.mi.com/app"


def _to_json(text: str) -> Any:
    cleaned = text.replace("&&&START&&&", "")
    try:
        return json.loads(cleaned)
    except ValueError:
        return {}


def _generate_device_id() -> str:
    return "".join(chr(random.randint(97, 122)) for _ in range(6))


def _generate_agent() -> str:
    agent_id = "".join(chr(random.randint(65, 69)) for _ in range(13))
    suffix = "".join(chr(random.randint(97, 122)) for _ in range(18))
    return f"{suffix}-{agent_id} APP/com.xiaomi.mihome APPV/10.5.201"


def _generate_nonce(millis: int) -> str:
    raw = os.urandom(8) + int(millis / 60000).to_bytes(4, byteorder="big")
    return base64.b64encode(raw).decode()


def _enc_signature(url: str, method: str, signed_nonce: str, params: dict[str, str]) -> str:
    parts = [str(method).upper(), url.split("com")[1].replace("/app/", "/")]
    parts.extend(f"{key}={value}" for key, value in params.items())
    parts.append(signed_nonce)
    return base64.b64encode(hashlib.sha1("&".join(parts).encode()).digest()).decode()


def _encrypt_rc4(password: str, payload: str) -> str:
    cipher = ARC4.new(base64.b64decode(password))
    cipher.encrypt(bytes(1024))
    return base64.b64encode(cipher.encrypt(payload.encode())).decode()


def _decrypt_rc4(password: str, payload: str) -> str:
    cipher = ARC4.new(base64.b64decode(password))
    cipher.encrypt(bytes(1024))
    return cipher.encrypt(base64.b64decode(payload)).decode()


