"""Geometry builders and a two-tier detection helper shared by the engine tests.

The pipeline publishes two models from one reading of a document. `detect`
returns both, joined into one mapping so an assertion can reach a value and the
note catalogue without knowing which artifact carried it; `values_model` and
`structure_model` are there for the tests that care about the separation.
"""
from __future__ import annotations

from engines.financial.detector import detect_financial_structure
from engines.values.detector import detect_values
from engines.values.lines import prepare


class Detected(dict):
    """The two published models, joined for assertion convenience."""

    values_model: dict
    structure_model: dict
    diagnostics: dict


def line(text: str, *, y: float, line_index: int, height: float = 0.012, x: float = 0.02) -> list[dict]:
    return [
        {
            "char": char,
            "x": x + index * 0.008,
            "y": y,
            "width": 0.008,
            "height": height,
            "lineIndex": line_index,
        }
        for index, char in enumerate(text)
    ]


def cell(text: str, *, y: float, line_index: int, x: float, height: float = 0.012) -> list[dict]:
    return line(text, y=y, line_index=line_index, height=height, x=x)


def geometry(pages: list[list[dict]]) -> dict:
    return {
        "version": 1,
        "coordinateSpace": "normalized",
        "pages": [
            {"pageIndex": index, "characters": characters}
            for index, characters in enumerate(pages)
        ],
    }


def compact_geometry(model: dict) -> dict:
    """Store a text-geometry model with per-character arrays, for the shared corpus.

    The corpus is read by both the Python and TypeScript suites, so it is kept in
    a compact form -- one short array per character instead of a six-key object --
    and each runner expands it back to text-geometry-v1 with `expand_geometry`.
    """
    return {
        "version": model["version"],
        "coordinateSpace": model["coordinateSpace"],
        "pages": [
            {
                "pageIndex": page["pageIndex"],
                "characters": [
                    [char["char"], char["x"], char["y"], char["width"], char["height"], char["lineIndex"]]
                    for char in page["characters"]
                ],
            }
            for page in model["pages"]
        ],
    }


def expand_geometry(compact: dict) -> dict:
    """Expand the compact character arrays back into a text-geometry-v1 model."""
    return {
        "version": compact["version"],
        "coordinateSpace": compact["coordinateSpace"],
        "pages": [
            {
                "pageIndex": page["pageIndex"],
                "characters": [
                    {"char": c[0], "x": c[1], "y": c[2], "width": c[3], "height": c[4], "lineIndex": c[5]}
                    for c in page["characters"]
                ],
            }
            for page in compact["pages"]
        ],
    }


def detect(model: dict, *, tables: dict | None = None) -> Detected:
    """Run both tiers over one text-geometry model."""
    document = prepare(model)
    structure = detect_financial_structure(document, tables=tables)
    diagnostics: dict = {}
    values = detect_values(document, claims=structure.spans, diagnostics=diagnostics)

    joined = Detected(values)
    joined.update(
        {
            "documentClass": structure.model["documentClass"],
            "apparatus": structure.model["apparatus"],
            "notes": structure.model["notes"],
            "noteReferences": structure.model["noteReferences"],
            "items": structure.model["items"],
            "itemReferences": structure.model["itemReferences"],
        }
    )
    joined.values_model = values
    joined.structure_model = structure.model
    joined.diagnostics = diagnostics
    return joined


def detect_pages(pages: list[list[dict]]) -> Detected:
    return detect(geometry(pages))


def published(model: dict) -> list[str]:
    return [value["text"] for page in model["pages"] for value in page["values"]]


def references(model: dict) -> list[dict]:
    return [item for page in model["pages"] for item in page.get("references", [])]


def structure(model: dict) -> list[dict]:
    return [item for page in model["pages"] for item in page.get("structure", [])]


def noise(model: dict) -> list[dict]:
    return [item for page in model["pages"] for item in page.get("noise", [])]


def refused(model: dict) -> list[dict]:
    """Everything the detector declined to publish as a value or a reference."""
    return structure(model) + noise(model)


def label(item: dict) -> str:
    """What a refusal called itself: a noise reason, or a structure kind."""
    return item.get("reason") or item["kind"]


def canonical(model: dict) -> dict:
    """The cross-runtime comparable form of a values model.

    Derived span ids and the detector version are dropped, and every optional
    layer is materialized as a list, so the Python engine and the TypeScript
    recognizer can be scored against one shared corpus. The TypeScript side
    mirrors this function exactly; keeping it this small is what keeps the two
    from drifting.
    """
    def strip(span: dict) -> dict:
        return {key: value for key, value in span.items() if key != "id"}

    return {
        "documentContext": model["documentContext"],
        "pages": [
            {
                "pageIndex": page["pageIndex"],
                "context": page["context"],
                "values": [strip(span) for span in page.get("values", [])],
                "references": [strip(span) for span in page.get("references", [])],
                "structure": [strip(span) for span in page.get("structure", [])],
                "noise": [strip(span) for span in page.get("noise", [])],
            }
            for page in model["pages"]
        ],
    }
