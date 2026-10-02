"""Load the shared value-recognition config once and expose it typed.

The config lives in `contracts/value-recognition-config-v1.json` -- the single
source of truth shared with the TypeScript frontend recognizer. A change to
what values get captured is made there once and reflected in both runtimes.

It is located by walking up from this file, so it resolves in development and
in the shipped worker bundle alike (`build-worker.ps1` copies `contracts/`
beside `engines/`). The dialect-specific regular expressions stay in their
module; only the vocabulary, policy, tables and thresholds come from here.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_CONFIG_FILENAME = "value-recognition-config-v1.json"


def _find_config() -> Path:
    start = Path(__file__).resolve()
    for directory in (start.parent, *start.parents):
        for candidate in (
            directory / "contracts" / _CONFIG_FILENAME,
            directory / _CONFIG_FILENAME,
        ):
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(f"{_CONFIG_FILENAME} not found above {start}")


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(_find_config().read_text(encoding="utf-8"))


def load() -> dict:
    """The raw config mapping. Prefer the typed constants below."""
    return _load()


_cfg = _load()

# --- vocabulary (closed enums) ------------------------------------------------
CATEGORIES = tuple(_cfg["categories"])
REFERENCE_KINDS = tuple(_cfg["referenceKinds"])
STRUCTURE_KINDS = tuple(_cfg["structureKinds"])
NOISE_REASONS = tuple(_cfg["noiseReasons"])

CLICKABLE_BY_DEFAULT = dict(_cfg["clickable"]["byDefault"])
CLICKABLE_KINDS = dict(_cfg["clickable"]["byKind"])

# --- recognition tables -------------------------------------------------------
CURRENCY_CODES = {code: code for code in _cfg["currencies"]["codes"]}
CURRENCY_SYMBOLS = dict(_cfg["currencies"]["symbols"])
MAGNITUDES = {key: int(value) for key, value in _cfg["magnitudes"].items()}
MONTHS = {key: int(value) for key, value in _cfg["months"].items()}

MONTH_PATTERN = _cfg["monthPattern"]
MAGNITUDE_PATTERN = _cfg["magnitudePattern"]

CURRENCY_CODE_PATTERN = "|".join(sorted(CURRENCY_CODES))
CURRENCY_SYMBOL_PATTERN = "[" + "".join(CURRENCY_SYMBOLS) + "]"

# --- confidence ---------------------------------------------------------------
CONFIDENCE = _cfg["confidence"]

# --- evidence word fragments ---------------------------------------------------
IDENTIFIER_CUES = tuple(
    (entry["kind"], entry["fragment"]) for entry in _cfg["evidence"]["identifierCues"]
)
NUMBER_MARK_FRAGMENT = _cfg["evidence"]["numberMarkFragment"]
PROSE_WORDS = int(_cfg["evidence"]["proseWords"])
PERIOD_CONTEXT_FRAGMENT = _cfg["evidence"]["periodContextFragment"]
CITATION_CONTEXT_FRAGMENT = _cfg["evidence"]["citationContextFragment"]

# --- context scale bodies ------------------------------------------------------
SCALE_BODIES = tuple(
    (int(entry["scale"]), entry["body"]) for entry in _cfg["context"]["scaleBodies"]
)

# --- profile thresholds --------------------------------------------------------
SUPERSCRIPT_RATIO = float(_cfg["profile"]["superscriptRatio"])
REPRESENTATIVE_GLYPHS = int(_cfg["profile"]["representativeGlyphs"])
SEQUENCE_MINIMUM = int(_cfg["profile"]["sequenceMinimum"])
YEAR_RANGE = tuple(int(value) for value in _cfg["profile"]["yearRange"])
PLACE_TOLERANCE = int(_cfg["profile"]["placeTolerance"])
REPEAT_SHARE = float(_cfg["profile"]["repeatShare"])
REPEAT_MINIMUM = int(_cfg["profile"]["repeatMinimum"])
FUNCTION_WORDS = frozenset(_cfg["profile"]["functionWords"])

# --- list thresholds -----------------------------------------------------------
MIN_CHAIN = int(_cfg["lists"]["minChain"])
COLUMN_TOLERANCE = float(_cfg["lists"]["columnTolerance"])
INLINE_REACH = int(_cfg["lists"]["inlineReach"])
REFERENCE_REACH = int(_cfg["lists"]["referenceReach"])
MIN_INDICATORS = int(_cfg["lists"]["minIndicators"])
INDICATOR_RATIO = float(_cfg["lists"]["indicatorRatio"])
REPRESENTATIVE_LINES = int(_cfg["lists"]["representativeLines"])
MIN_PROSE_WORDS = int(_cfg["lists"]["minProseWords"])
BAND_REACH = int(_cfg["lists"]["bandReach"])
