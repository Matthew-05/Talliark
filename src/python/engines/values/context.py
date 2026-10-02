"""Infer conservative currency and magnitude context for detected values."""
from __future__ import annotations

import re
from collections import Counter

from .config import SCALE_BODIES
from .spans import CURRENCY_CODES, CURRENCY_SYMBOLS


_SCALE_PATTERNS = tuple(
    (scale, re.compile(rf"\b{body}\b", re.I)) for scale, body in SCALE_BODIES
)


def context_for_text(text: str) -> dict:
    context: dict = {}
    upper = text.upper()
    currencies = Counter()
    for code in CURRENCY_CODES:
        currencies[code] += len(re.findall(rf"\b{code}\b", upper))
    for symbol, code in CURRENCY_SYMBOLS.items():
        currencies[code] += text.count(symbol)
    if currencies:
        currency, count = currencies.most_common(1)[0]
        if count:
            context["currency"] = currency
    for scale, pattern in _SCALE_PATTERNS:
        if pattern.search(text):
            context["scale"] = scale
            break
    return context


def document_context(page_contexts: list[dict]) -> dict:
    currencies = Counter(context.get("currency") for context in page_contexts if context.get("currency"))
    result: dict = {}
    if currencies:
        result["currency"] = currencies.most_common(1)[0][0]
    return result
