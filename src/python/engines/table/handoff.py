"""Stable, read-only table evidence for private downstream analysis.

The public table artifact is a JSON-shaped mutable dictionary because it crosses
the worker boundary.  Analytical engines must not receive that object directly:
an accidental write would make an on-demand scan change the cache-build result
in memory.  This module creates an isolated, recursively immutable snapshot and
is the only supported import surface for downstream engines that need the
general detector's fitted page layout or token placement.

This is an internal Python API, not a new cross-runtime contract.  The durable
shape remains ``table-structure-v1``.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias

from engines.table.grid import assign_tokens
from engines.table.layout import PageLayout, VisualLine, build_page_layout


JsonMapping: TypeAlias = Mapping[str, Any]
AnalysisLayout: TypeAlias = PageLayout
AnalysisLine: TypeAlias = VisualLine


@dataclass(frozen=True)
class AnalysisPage:
    """One page's fresh layout and immutable accepted general tables."""

    page_index: int
    layout: AnalysisLayout
    tables: tuple[JsonMapping, ...]


@dataclass(frozen=True)
class AnalysisHandoff:
    """An isolated snapshot of the evidence a downstream analysis may read."""

    detector_version: str
    source_digest: str
    pages: tuple[AnalysisPage, ...]


def artifact_digest(artifact: dict | None) -> str:
    """Content digest used to prove the caller's artifact was not rewritten."""
    payload = json.dumps(
        artifact or {},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def table_geometry_digest(table: JsonMapping) -> str:
    """Identity of the general geometry a private fallback was derived from."""
    geometry = {
        key: table.get(key)
        for key in ("id", "bounds", "columns", "rows", "header", "rulings")
    }
    payload = json.dumps(
        _thaw(geometry),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_analysis_handoff(
    geometry: dict,
    general_tables: dict | None,
) -> AnalysisHandoff:
    """Build the general engine's supported read-only analysis surface."""
    detected_by_page = {
        int(page["pageIndex"]): tuple(_freeze(table) for table in page.get("tables", []))
        for page in (general_tables or {}).get("pages", [])
    }
    pages = tuple(
        AnalysisPage(
            page_index=int(page_geometry["pageIndex"]),
            layout=build_page_layout(page_geometry),
            tables=detected_by_page.get(int(page_geometry["pageIndex"]), ()),
        )
        for page_geometry in geometry.get("pages", [])
    )
    return AnalysisHandoff(
        detector_version=str((general_tables or {}).get("detectorVersion", "unavailable")),
        source_digest=artifact_digest(general_tables),
        pages=pages,
    )


def assign_line_tokens(
    line: AnalysisLine,
    boundaries: list[float],
) -> list[list]:
    """Apply the exact token placement used to fit the published grid."""
    return assign_tokens(line, boundaries)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


__all__ = [
    "AnalysisHandoff",
    "AnalysisLayout",
    "AnalysisLine",
    "AnalysisPage",
    "JsonMapping",
    "artifact_digest",
    "assign_line_tokens",
    "build_analysis_handoff",
    "table_geometry_digest",
]
