"""
Translations API: ``/api/translations/...``.

Replaces ``gramps_webapi/api/resources/translations.py``. Translations are
looked up in the static ``msgid -> msgstr`` dictionaries in
``data/translations/<lang>.json`` generated from the Gramps ``.po`` catalogs
by ``scripts/generate_gramps_data.py``. Frontend-only strings are loaded by
gramps-web itself from ``/lang/<lang>.json``, so only Gramps strings are
served here (as in gramps-web-api).
"""

import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

DATA_DIR = Path(__file__).resolve().parent / "data" / "translations"

# English language names as in gramps.gen.utils.win32locale._LOCALE_NAMES.
LANGUAGE_NAMES = {
    "ar": "Arabic",
    "bg": "Bulgarian",
    "br": "Breton",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "de_AT": "German (Austria)",
    "el": "Greek",
    "en": "English (USA)",
    "en_GB": "English",
    "eo": "Esperanto",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "ga": "Gaelic",
    "he": "Hebrew",
    "hr": "Croatian",
    "hu": "Hungarian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "lt": "Lithuanian",
    "mk": "Macedonian",
    "nb": "Norwegian Bokmal",
    "nl": "Dutch",
    "nn": "Norwegian Nynorsk",
    "pl": "Polish",
    "pt_BR": "Portuguese (Brazil)",
    "pt_PT": "Portuguese (Portugal)",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sq": "Albanian",
    "sr": "Serbian",
    "sv": "Swedish",
    "ta": "Tamil",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh_CN": "Chinese (Simplified)",
    "zh_HK": "Chinese (Hong Kong)",
    "zh_TW": "Chinese (Traditional)",
}

CONTEXT_SEPARATOR = "|"


@lru_cache(maxsize=1)
def available_languages() -> list:
    """Language codes with a catalog: English plus every generated dictionary."""
    codes = {"en"}
    if DATA_DIR.is_dir():
        codes.update(p.stem for p in DATA_DIR.glob("*.json"))
    return sorted(codes)


@lru_cache(maxsize=None)
def load_catalog(language: str) -> dict:
    """Load the msgid -> msgstr dictionary of a language (empty for English)."""
    path = DATA_DIR / f"{language}.json"
    if language == "en" or not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def normalize_language(language) -> str | None:
    """Map ``fi``, ``fi_FI``, ``fi-FI.UTF-8``... to an available code, or None."""
    if not language:
        return None
    language = str(language).split(".")[0].replace("-", "_")
    available = available_languages()
    if language in available:
        return language
    base = language.split("_")[0]
    if base in available:
        return base
    if base == "en":
        return "en"
    return None


def default_language() -> str:
    """The tree's language (``settings.GRAMPS_LANGUAGE``) or English."""
    return normalize_language(getattr(settings, "GRAMPS_LANGUAGE", None)) or "en"


def translate(string: str, language: str) -> str:
    """
    Translate one string like ``GrampsLocale.translation.sgettext``.

    A ``"context\\x04msgid"`` or ``"context|msgid"`` string is looked up with
    its context first; when no translation exists, the context is stripped
    from the returned (English) string. Unknown languages pass through.
    """
    if not isinstance(string, str) or not string.strip():
        return string
    catalog = load_catalog(normalize_language(language) or "en")
    if "\x04" in string:
        context, msgid = string.split("\x04", 1)
        return catalog.get(f"{context}{CONTEXT_SEPARATOR}{msgid}") or catalog.get(msgid, msgid)
    if string in catalog:
        return catalog[string]
    if CONTEXT_SEPARATOR in string:
        return string.rsplit(CONTEXT_SEPARATOR, 1)[-1]
    return string


def language_entries() -> list:
    """The ``/api/translations/`` list in gramps-web-api format."""
    current = default_language()
    entries = []
    for code in available_languages():
        default = LANGUAGE_NAMES.get(code, code)
        entries.append(
            {
                "default": default,
                "current": translate(default, current),
                "language": code,
                "native": translate(default, code),
            }
        )
    return entries


def _error(message: str, status: int) -> Response:
    return Response({"error": {"message": message}}, status=status)


class TranslationsListView(APIView):
    """
    GET /api/translations/

    List the available languages: ``[{"default", "current", "language",
    "native"}]``. ``?sort=`` accepts comma separated keys ``current``,
    ``default``, ``language`` and ``native`` (prefix ``-`` for descending).
    """

    permission_classes = [AllowAny]

    def get(self, request):
        entries = language_entries()
        sort_param = request.query_params.get("sort") or "language"
        for key in sort_param.split(","):
            key = key.strip()
            reverse = key.startswith("-")
            key = key.lstrip("-")
            if key not in ("current", "default", "language", "native"):
                return _error(f"Invalid sort key: {key}", 422)
            entries.sort(key=lambda x, k=key: str(x[k]).lower(), reverse=reverse)
        return Response(entries)


class TranslationsDetailView(APIView):
    """
    GET  /api/translations/<language>?strings=["Birth", "Death"]
    POST /api/translations/<language>  body ``{"strings": [...]}``

    Returns ``[{"original": s, "translation": t}]``. Unknown languages
    return the originals (English).
    """

    permission_classes = [AllowAny]

    def get(self, request, language):
        raw = request.query_params.get("strings")
        if raw is None:
            return _error("Missing required query parameter: strings", 400)
        try:
            strings = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return _error("Error parsing strings", 400)
        return self._translate(strings, language)

    def post(self, request, language):
        data = request.data if isinstance(request.data, dict) else {}
        strings = data.get("strings")
        if strings is None:
            return _error("Missing required field: strings", 400)
        return self._translate(strings, language)

    def _translate(self, strings, language):
        if not isinstance(strings, list) or not all(isinstance(s, str) for s in strings):
            return _error("strings must be a list of strings", 400)
        return Response(
            [{"original": s, "translation": translate(s, language)} for s in strings]
        )
