"""Data models for the Talliark Python worker protocol (python-worker-v1.json)."""
from __future__ import annotations

from typing import Protocol
from dataclasses import dataclass


@dataclass
class OcrJob:
    """Inbound job received from the C# host via stdin."""
    job_id: str
    command: str
    pdf_base64: str
    mode: str = "full"
    # Reconcile is a scan, never a background pass: this is set only because a
    # user chose to run one. It appends the scan to this job rather than adding
    # a command, so the whole pipeline stays one job id, one progress stream and
    # one cancellation.
    analysis: bool = False
    detect_tables: bool = False
    document_id: str = ""
    version_id: str = ""

    @staticmethod
    def from_dict(d: dict) -> "OcrJob":
        mode = d.get("mode", "full")
        if mode not in ("full", "geometry-only"):
            mode = "full"
        return OcrJob(
            job_id=d["job_id"],
            command=d["command"],
            pdf_base64=d["pdf_base64"],
            mode=mode,
            analysis=bool(d.get("analysis", False)),
            detect_tables=bool(d.get("detect_tables", False)),
            document_id=str(d.get("document_id", "")),
            version_id=str(d.get("version_id", "")),
        )


@dataclass
class OcrResult:
    """Outbound result written to stdout."""
    job_id: str
    status: str          # "success" | "error"
    pdf_base64: str = "" # populated on full OCR success
    geometry_base64: str = ""
    table_structure_base64: str = ""
    document_values_base64: str = ""
    financial_structure_base64: str = ""
    # Present only when the job asked for analysis. Absent on an ordinary cache
    # build, because Reconcile does not run in one.
    reconcile_base64: str = ""
    error: str = ""      # populated on error
    # OcrDiagnostics per contracts/python-worker-v1.json. Host debug logging
    # only — the host must tolerate this being absent and must not branch on it.
    diagnostics: dict = None

    def to_dict(self) -> dict:
        d: dict = {"job_id": self.job_id, "status": self.status}
        if self.status == "success":
            if self.pdf_base64:
                d["pdf_base64"] = self.pdf_base64
            if self.geometry_base64:
                d["geometry_base64"] = self.geometry_base64
            if self.table_structure_base64:
                d["table_structure_base64"] = self.table_structure_base64
            if self.document_values_base64:
                d["document_values_base64"] = self.document_values_base64
            if self.financial_structure_base64:
                d["financial_structure_base64"] = self.financial_structure_base64
            if self.reconcile_base64:
                d["reconcile_base64"] = self.reconcile_base64
            if self.diagnostics:
                d["diagnostics"] = self.diagnostics
        else:
            d["error"] = self.error
            if self.diagnostics:
                d["diagnostics"] = self.diagnostics
        return d


@dataclass
class ConvertJob:
    """Inbound conversion job received from the C# host via stdin."""
    job_id: str
    command: str
    source_base64: str
    source_extension: str
    source_name: str = ""

    @staticmethod
    def from_dict(d: dict) -> "ConvertJob":
        return ConvertJob(
            job_id=d["job_id"],
            command=d["command"],
            source_base64=d["source_base64"],
            source_extension=d["source_extension"],
            source_name=d.get("source_name", ""),
        )


@dataclass
class ConvertResult:
    """Outbound conversion result written to stdout.

    output_kind is "pdf" when the worker produced finished PDF bytes, or "html"
    when it could only normalise the source (e.g. .eml) and the host must render
    the markup to PDF itself.
    """
    job_id: str
    status: str            # "success" | "error"
    output_kind: str = ""  # "pdf" | "html"
    pdf_base64: str = ""
    html: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        if self.status != "success":
            return {"job_id": self.job_id, "status": "error", "error": self.error}

        d: dict = {
            "job_id": self.job_id,
            "status": "success",
            "output_kind": self.output_kind,
        }
        if self.output_kind == "pdf":
            d["pdf_base64"] = self.pdf_base64
        else:
            d["html"] = self.html
        return d


class Stage:
    """
    The steps a job moves through, mirroring the ProgressStage enum in
    contracts/python-worker-v1.json. Declared in pipeline order, which is what
    lets a consumer place a step on a progress bar; an adaptive run skips
    steps, so the order is a ranking, not a sequence to expect in full.

    QUEUE, PREPARE, TRANSFER and FINALIZING belong to the host and are never
    emitted here. They are listed so that STAGE_ORDER is the whole pipeline.
    """

    QUEUE = "queue"
    PREPARE = "prepare"
    CONVERT = "convert"
    TRANSFER = "transfer"
    SECURITY = "security"
    SOURCE_CHECK = "source-check"
    SOURCE = "source"
    GEOMETRY = "geometry"
    OCR = "ocr"
    ORIENTATION = "orientation"
    ADAPTIVE_OCR = "adaptive-ocr"
    TABLE_RECOVERY = "table-recovery"
    TABLE_STRUCTURE = "table-structure"
    FINANCIAL_STRUCTURE = "financial-structure"
    VALUES = "values"
    # The scan's two stages. They run only when a job asks for analysis, which
    # is exactly the gap the enum's contract tells consumers to tolerate.
    RECONCILE_TABLES = "reconcile-tables"
    RECONCILE = "reconcile"
    RESULT_TRANSFER = "result-transfer"
    FINALIZING = "finalizing"


STAGE_ORDER: tuple[str, ...] = (
    Stage.QUEUE,
    Stage.PREPARE,
    Stage.CONVERT,
    Stage.TRANSFER,
    Stage.SECURITY,
    Stage.SOURCE_CHECK,
    Stage.SOURCE,
    Stage.GEOMETRY,
    Stage.OCR,
    Stage.ORIENTATION,
    Stage.ADAPTIVE_OCR,
    Stage.TABLE_RECOVERY,
    Stage.TABLE_STRUCTURE,
    Stage.FINANCIAL_STRUCTURE,
    Stage.VALUES,
    Stage.RECONCILE_TABLES,
    Stage.RECONCILE,
    Stage.RESULT_TRANSFER,
    Stage.FINALIZING,
)


class ProgressReporter(Protocol):
    """
    How every engine reports progress. The stage is required because it is the
    fact a consumer renders; the message beside it is prose for a human and may
    be reworded without breaking anything. Counts are optional and count within
    the stage only.
    """

    def __call__(
        self,
        message: str,
        stage: str,
        *,
        current: int | None = None,
        total: int | None = None,
        unit: str | None = None,
    ) -> None: ...


@dataclass
class OcrProgress:
    """Intermediate progress message written to stdout during processing."""
    job_id: str
    message: str
    stage: str
    current: int | None = None
    total: int | None = None
    unit: str | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "job_id": self.job_id,
            "status": "progress",
            "message": self.message,
            "stage": self.stage,
        }
        # current and total travel together or not at all; the contract pins
        # unit to the pair as well.
        if self.current is not None and self.total is not None and self.total > 0:
            d["current"] = max(0, min(self.current, self.total))
            d["total"] = self.total
            if self.unit is not None:
                d["unit"] = self.unit
        return d
