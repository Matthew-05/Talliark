"""Exact arithmetic for an explicit signed equation; no discovery or admission."""
from __future__ import annotations

from decimal import Decimal, localcontext
import re

DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
# A bounded input size avoids pathological manual strings. The arithmetic
# context is derived from the actual digits and number of operands, not 28.
MAX_VALUE_CHARACTERS = 256


def decimal_value(value: str) -> Decimal:
    if not isinstance(value, str) or len(value) > MAX_VALUE_CHARACTERS or not DECIMAL.fullmatch(value):
        raise ValueError("Enter an exact decimal without separators or exponent notation.")
    return Decimal(value)


def evaluate(target: str, operands: list[tuple[str, int]]) -> dict:
    if not target or len(operands) < 2 or any(not value for value, _ in operands):
        return {"state": "not-evaluable", "sum": "", "delta": "", "reason": "Select a result and at least two readable operands."}
    values = [decimal_value(target), *(decimal_value(value) for value, _ in operands)]
    if any(type(sign) is not int or sign not in (-1, 1) for _, sign in operands):
        raise ValueError("An operand must be added or subtracted.")
    fractional = max(max(0, -value.as_tuple().exponent) for value in values)
    integral = max(max(1, value.adjusted() + 1) for value in values)
    with localcontext() as context:
        context.prec = integral + fractional + len(str(len(values))) + 2
        total = sum((values[i + 1] * sign for i, (_, sign) in enumerate(operands)), Decimal(0))
        delta = values[0] - total
        return {"state": "exact-match" if delta == 0 else "difference", "sum": format(total, "f"), "delta": format(delta, "f"), "reason": ""}
