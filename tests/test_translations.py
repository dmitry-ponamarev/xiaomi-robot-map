"""Guard the shipped translations.

Home Assistant takes step titles, descriptions and field labels from
``translations/<lang>.json``. A missing key silently falls back to the raw key
(``captcha_code``) and an empty description - which is exactly what a user sees
as "there is no captcha image". These tests make such a gap impossible to ship.
"""

from __future__ import annotations

import json
import pathlib

import pytest

BASE = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "xiaomi_robot_map"

# step id -> field keys rendered by the matching schema
CONFIG_STEPS: dict[str, list[str]] = {
    "user": ["source"],
    "cloud": ["username", "password", "country"],
    "captcha": ["captcha_code"],
    "two_factor": [],
    "local": ["url"],
    "device": ["device_id", "host"],
    "manual": ["host", "token"],
    "reauth_confirm": ["username", "password"],
}

ERRORS = [
    "invalid_auth",
    "cannot_connect",
    "invalid_captcha",
    "two_factor",
    "captcha_required",
    "invalid_format",
    "device_not_found",
]


def _load(lang: str) -> dict:
    path = BASE / "translations" / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_config_flow_steps_are_translated(lang: str) -> None:
    steps = _load(lang)["config"]["step"]
    missing = []
    for step, fields in CONFIG_STEPS.items():
        if step not in steps:
            missing.append(f"{step} (step)")
            continue
        data = steps[step]
        if not data.get("title"):
            missing.append(f"{step}.title")
        if not data.get("description"):
            missing.append(f"{step}.description")
        for field in fields:
            if not (data.get("data") or {}).get(field):
                missing.append(f"{step}.data.{field}")
    assert not missing, f"{lang}: untranslated keys {missing}"


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_errors_are_translated(lang: str) -> None:
    errors = _load(lang)["config"]["error"]
    missing = [key for key in ERRORS if not errors.get(key)]
    assert not missing, f"{lang}: untranslated errors {missing}"


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_captcha_description_has_placeholders(lang: str) -> None:
    description = _load(lang)["config"]["step"]["captcha"]["description"]
    assert "{image_markdown}" in description
    assert "{image_url}" in description


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_two_factor_description_has_url(lang: str) -> None:
    description = _load(lang)["config"]["step"]["two_factor"]["description"]
    assert "{url}" in description


def test_english_matches_strings_json() -> None:
    """``strings.json`` is the English source of truth shipped to translators."""
    strings = json.loads((BASE / "strings.json").read_text(encoding="utf-8"))
    strings.pop("title", None)
    assert strings == _load("en")
