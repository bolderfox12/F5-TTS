"""Offline UI translations; project text, names, and model IDs are never translated."""

import json
import re
from functools import lru_cache
from pathlib import Path
from string import Formatter


INTERFACE_LANGUAGES = {
    "pt-BR": "Português (Brasil)",
    "en-US": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "zh-CN": "简体中文",
    "ja": "日本語",
}
LOCALE_ORDER = tuple(INTERFACE_LANGUAGES)


@lru_cache(maxsize=1)
def catalogs():
    rows = json.loads((Path(__file__).parent / "locales" / "messages.json").read_text(encoding="utf-8"))
    return {locale: {row[0]: row[index] for row in rows} for index, locale in enumerate(LOCALE_ORDER)}


@lru_cache(maxsize=1)
def message_patterns():
    result = []
    for source in catalogs()["pt-BR"]:
        parts = list(Formatter().parse(source))
        if not any(field for _text, field, _spec, _conversion in parts):
            continue
        pattern = ""
        seen = set()
        for text, field, _spec, _conversion in parts:
            pattern += re.escape(text)
            if field:
                pattern += f"(?P={field})" if field in seen else f"(?P<{field}>.+?)"
                seen.add(field)
        result.append((re.compile("^" + pattern + "$", re.DOTALL), source))
    return result


def translate(text, locale="pt-BR", **values):
    text = str(text)
    messages = catalogs().get(locale, catalogs()["pt-BR"])
    if text in messages:
        translated = messages[text]
        return translated.format(**values) if values else translated
    if values:
        return text.format(**values)
    if locale != "pt-BR":
        for pattern, source in message_patterns():
            match = pattern.fullmatch(text)
            if match:
                try:
                    return messages[source].format(**match.groupdict())
                except (ValueError, TypeError, KeyError):
                    return text
    return text


def localized_options(options, translator):
    return {key: translator(label) for key, label in options.items()}
