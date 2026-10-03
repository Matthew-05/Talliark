"""Compact JSON transport encoding shared by OCR-adjacent engines."""
from __future__ import annotations

import base64
import gzip
import json


def json_to_base64(value: dict) -> str:
    """Serialize compact JSON, gzip it, and return its base64 representation."""
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(gzip.compress(payload)).decode("ascii")


def json_from_base64(value: str) -> dict:
    """Decode a gzip-compressed, base64-encoded JSON object."""
    payload = gzip.decompress(base64.b64decode(value))
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Encoded JSON payload must contain an object.")
    return decoded
