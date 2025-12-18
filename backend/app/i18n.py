from __future__ import annotations

import re
from typing import Mapping

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "es")

_template_pattern = re.compile(r"{(\w+)}")


def normalize_locale(value: str | None, fallback: str = DEFAULT_LOCALE) -> str:
    """Normalize a locale/language code to the small set we currently support."""
    if not value:
        return fallback
    lowered = value.strip().lower()
    if lowered.startswith("es"):
        return "es"
    return fallback


def t(
    strings_by_locale: Mapping[str, Mapping[str, str]],
    locale: str,
    key: str,
    fallback: str | None = None,
) -> str:
    table = strings_by_locale.get(locale) or strings_by_locale.get(DEFAULT_LOCALE) or {}
    if key in table:
        return table[key]
    english = strings_by_locale.get(DEFAULT_LOCALE) or {}
    if key in english:
        return english[key]
    return fallback if fallback is not None else key


def format_template(
    template: str, variables: Mapping[str, object] | None = None
) -> str:
    vars_map = variables or {}

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in vars_map:
            return str(vars_map[key])
        return match.group(0)

    return _template_pattern.sub(_replace, template or "")
