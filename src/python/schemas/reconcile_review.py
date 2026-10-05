"""Validate the closed review schema without adding a shipped dependency.

The generated schema is the sole shape definition. This small interpreter
supports the validation keywords used by the review definitions.
"""
from __future__ import annotations

import math
from .reconcile_review_generated import SCHEMA


def validate(value, name: str) -> None:
    _validate(value, SCHEMA["definitions"][name], name)


def _validate(value, schema, path):
    if "$ref" in schema:
        return _validate(value, SCHEMA["definitions"][schema["$ref"].split("/")[-1]], path)
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ValueError(f"{path}: unsupported constant")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: unsupported value")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if kind in types and type(value) is not types[kind]:
        raise ValueError(f"{path}: expected {kind}")
    if kind == "number" and (type(value) not in (int, float) or not math.isfinite(value)):
        raise ValueError(f"{path}: expected finite number")
    if kind == "object":
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            raise ValueError(f"{path}: missing {', '.join(sorted(missing))}")
        if schema.get("additionalProperties") is False and value.keys() - schema["properties"].keys():
            raise ValueError(f"{path}: unknown properties")
        for key, field in value.items():
            _validate(field, schema["properties"][key], f"{path}.{key}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise ValueError(f"{path}: invalid item count")
        if schema.get("uniqueItems") and any(item in value[:i] for i, item in enumerate(value)):
            raise ValueError(f"{path}: duplicate entries")
        for i, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{i}]")
    elif kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            raise ValueError(f"{path}: invalid string length")
    elif kind in ("integer", "number"):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ValueError(f"{path}: outside allowed range")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ValueError(f"{path}: outside allowed range")
