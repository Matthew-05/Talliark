"""Talliark worker — stdin/stdout JSON-line protocol.

OCR is geometry-first: the worker sanitizes the visual PDF, recognizes only the
pages that need OCR, and returns text geometry beside the passive PDF. It never
writes an OCR text layer into the PDF.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time

import pymupdf as fitz

from engines.conversion_engine import ConversionError, convert_to_pdf
from engines.geometry_engine import (
    extract_text_geometry,
    find_coincident_text_layer_pages,
    find_unstrippable_hidden_text_pages,
    geometry_to_base64,
)
from engines.ocr_engine import (
    active_ocr_engine,
    detect_direct_page_rotations,
    extract_direct_text_geometry,
    merge_geometry_pages,
    merge_missing_text_lines,
    needs_adaptive_retry,
    needs_garbled_text_retry,
    select_pages_requiring_ocr,
    should_merge_faint_ink_retry,
    should_select_rotated_retry,
    summarize_geometry_quality,
)
from engines.pdf_security import sanitize_pdf_bytes
from engines.financial.detector import detect_financial_structure, structure_to_base64 as financial_structure_to_base64
from engines.values.detector import detect_values, values_to_base64
from engines.reconcile.detector import detect_reconcile, reconcile_to_base64
from engines.values.lines import prepare as prepare_lines
from engines.table.detector import detect_tables, structure_to_base64
from engines.table_cell_engine import recover_table_geometry
from schemas.models import (
    ConvertJob,
    ConvertResult,
    OcrJob,
    OcrProgress,
    OcrResult,
    Stage,
)


def _claim_protocol_stream():
    """Reserve stdout for NDJSON and discard accidental library writes."""
    try:
        protocol_fd = os.dup(1)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, 1)
        os.close(devnull_fd)
        stream = os.fdopen(protocol_fd, "w", encoding="utf-8", newline="\n")
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        return stream
    except OSError:
        return sys.stdout


_PROTOCOL_OUT = _claim_protocol_stream()


def _write(obj: dict) -> None:
    _PROTOCOL_OUT.write(json.dumps(obj, separators=(",", ":")) + "\n")
    _PROTOCOL_OUT.flush()


def _handle_convert_job(job: ConvertJob) -> None:
    def on_progress(
        message: str,
        stage: str,
        *,
        current: int | None = None,
        total: int | None = None,
        unit: str | None = None,
    ) -> None:
        _write(
            OcrProgress(
                job_id=job.job_id,
                message=message,
                stage=stage,
                current=current,
                total=total,
                unit=unit,
            ).to_dict()
        )

    try:
        source_bytes = base64.b64decode(job.source_base64)
        output = convert_to_pdf(
            source_bytes,
            job.source_extension,
            source_name=job.source_name,
            progress_callback=on_progress,
        )
        if output.kind == "pdf":
            _write(
                ConvertResult(
                    job_id=job.job_id,
                    status="success",
                    output_kind="pdf",
                    pdf_base64=base64.b64encode(output.pdf_bytes).decode("ascii"),
                ).to_dict()
            )
        else:
            _write(
                ConvertResult(
                    job_id=job.job_id,
                    status="success",
                    output_kind="html",
                    html=output.html,
                ).to_dict()
            )
    except ConversionError as exc:
        _write(ConvertResult(job_id=job.job_id, status="error", error=str(exc)).to_dict())
    except Exception as exc:  # noqa: BLE001
        _write(
            ConvertResult(
                job_id=job.job_id,
                status="error",
                error=f"Conversion failed: {exc}",
            ).to_dict()
        )


def _run_direct_ocr(
    pdf_bytes: bytes,
    page_numbers: list[int],
    progress_callback,
) -> tuple[dict[int, dict], dict[int, dict], dict]:
    """Recognize selected pages and apply the direct geometry retry ladder."""
    diagnostics = {
        "evaluated_profiles": ["direct-hocr-300"],
        "direct_retry_page_numbers": [],
        "direct_400_selected_page_numbers": [],
        "direct_rotated_page_numbers": [],
        "adaptive_ocr_ms": 0,
    }
    progress_callback(
        f"Direct OCR on {len(page_numbers)} page(s) at 300 DPI…", Stage.OCR
    )
    pages, stats = extract_direct_text_geometry(
        pdf_bytes,
        page_numbers,
        dpi=300,
        progress_callback=progress_callback,
    )

    orientation_candidates = [
        page_number
        for page_number, page_stats in stats.items()
        if float(page_stats["mean_confidence"]) < 75.0
        or int(page_stats["character_count"]) < 40
    ]
    rotations = detect_direct_page_rotations(
        pdf_bytes,
        orientation_candidates,
        progress_callback=progress_callback,
    )
    if rotations:
        diagnostics["evaluated_profiles"].append("direct-hocr-oriented-300")
        progress_callback(
            f"Retrying {len(rotations)} confidently sideways page(s) upright…",
            Stage.ADAPTIVE_OCR,
        )
        started = time.perf_counter()
        rotated_pages, rotated_stats = extract_direct_text_geometry(
            pdf_bytes,
            list(rotations),
            dpi=300,
            page_rotations=rotations,
            progress_callback=progress_callback,
            progress_stage=Stage.ADAPTIVE_OCR,
        )
        diagnostics["adaptive_ocr_ms"] += int((time.perf_counter() - started) * 1000)
        for page_number in sorted(rotations):
            if should_select_rotated_retry(stats[page_number], rotated_stats[page_number]):
                pages[page_number] = rotated_pages[page_number]
                stats[page_number] = rotated_stats[page_number]
                diagnostics["direct_rotated_page_numbers"].append(page_number)

    retry_pages = [
        page_number
        for page_number, page_stats in stats.items()
        if float(page_stats["mean_confidence"]) < 75.0
        or int(page_stats["character_count"]) < 40
    ]
    diagnostics["direct_retry_page_numbers"] = retry_pages

    faint_pages = [
        page_number
        for page_number in retry_pages
        if int(stats[page_number]["character_count"]) >= 40
        and float(stats[page_number]["mean_confidence"]) < 75.0
    ]
    if faint_pages:
        diagnostics["evaluated_profiles"].append("direct-hocr-faint-ink-psm6-300")
        progress_callback(
            f"Enhancing {len(faint_pages)} faint or uneven scan page(s)…",
            Stage.ADAPTIVE_OCR,
        )
        started = time.perf_counter()
        enhanced_pages, enhanced_stats = extract_direct_text_geometry(
            pdf_bytes,
            faint_pages,
            dpi=300,
            psm=6,
            preprocessing="faint-ink",
            progress_callback=progress_callback,
            progress_stage=Stage.ADAPTIVE_OCR,
        )
        diagnostics["adaptive_ocr_ms"] += int((time.perf_counter() - started) * 1000)
        for page_number in faint_pages:
            first = stats[page_number]
            enhanced = enhanced_stats[page_number]
            if not should_merge_faint_ink_retry(first, enhanced):
                continue
            merged, changed_lines, word_delta, character_delta = merge_missing_text_lines(
                pages[page_number],
                enhanced_pages[page_number],
                first.get("line_stats"),
                enhanced.get("line_stats"),
            )
            if changed_lines > 0:
                pages[page_number] = merged
                first["word_count"] = int(first["word_count"]) + word_delta
                first["character_count"] = int(first["character_count"]) + character_delta

    sparse_pages = [
        page_number
        for page_number in retry_pages
        if int(stats[page_number]["character_count"]) < 40
    ]
    if sparse_pages:
        diagnostics["evaluated_profiles"].append("direct-hocr-400")
        progress_callback(
            f"Retrying {len(sparse_pages)} sparse page(s) at 400 DPI…",
            Stage.ADAPTIVE_OCR,
        )
        started = time.perf_counter()
        high_res_pages, high_res_stats = extract_direct_text_geometry(
            pdf_bytes,
            sparse_pages,
            dpi=400,
            progress_callback=progress_callback,
            progress_stage=Stage.ADAPTIVE_OCR,
        )
        diagnostics["adaptive_ocr_ms"] += int((time.perf_counter() - started) * 1000)
        for page_number in sparse_pages:
            first = stats[page_number]
            retry = high_res_stats[page_number]
            if (
                int(retry["character_count"]) > int(first["character_count"]) * 1.05
                or float(retry["mean_confidence"]) > float(first["mean_confidence"]) + 2.0
            ):
                pages[page_number] = high_res_pages[page_number]
                stats[page_number] = retry
                diagnostics["direct_400_selected_page_numbers"].append(page_number)

    remaining_sparse = [
        page_number
        for page_number in page_numbers
        if int(stats[page_number]["word_count"]) == 0
        or int(stats[page_number]["character_count"]) < 40
    ]
    if remaining_sparse:
        diagnostics["evaluated_profiles"].append("direct-hocr-cropped-psm4-300")
        progress_callback(
            f"Retrying {len(remaining_sparse)} empty or sparse page(s) "
            "with cropped single-column OCR…",
            Stage.ADAPTIVE_OCR,
        )
        started = time.perf_counter()
        cropped_pages, cropped_stats = extract_direct_text_geometry(
            pdf_bytes,
            remaining_sparse,
            dpi=300,
            psm=4,
            crop_to_dominant_image=True,
            progress_callback=progress_callback,
            progress_stage=Stage.ADAPTIVE_OCR,
        )
        diagnostics["adaptive_ocr_ms"] += int((time.perf_counter() - started) * 1000)
        for page_number in remaining_sparse:
            first = stats[page_number]
            retry = cropped_stats[page_number]
            if (
                int(retry["character_count"]) > int(first["character_count"]) * 1.05
                or float(retry["mean_confidence"]) > float(first["mean_confidence"]) + 2.0
            ):
                pages[page_number] = cropped_pages[page_number]
                stats[page_number] = retry

    return pages, stats, diagnostics


def _handle_job(job: OcrJob) -> None:
    def on_progress(
        message: str,
        stage: str,
        *,
        current: int | None = None,
        total: int | None = None,
        unit: str | None = None,
    ) -> None:
        _write(
            OcrProgress(
                job_id=job.job_id,
                message=message,
                stage=stage,
                current=current,
                total=total,
                unit=unit,
            ).to_dict()
        )

    started_at = time.perf_counter()
    diagnostics: dict = {
        "ocr_engine": "none",
        "rasterizer": "none",
        "mode": "none",
        "selected_profile": "none",
        "evaluated_profiles": [],
        "quality_warning": False,
        "pdf_sanitized": False,
        "removed_embedded_files": 0,
        "removed_links": 0,
    }
    page_count = 0

    def elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        decode_started = time.perf_counter()
        source_bytes = base64.b64decode(job.pdf_base64)
        diagnostics["decode_ms"] = elapsed_ms(decode_started)
        diagnostics["input_bytes"] = len(source_bytes)
        if not source_bytes:
            raise ValueError("PDF is empty.")

        sanitize_started = time.perf_counter()
        sanitized = sanitize_pdf_bytes(source_bytes, progress_callback=on_progress)
        pdf_bytes = sanitized.pdf_bytes
        diagnostics["input_sanitize_ms"] = elapsed_ms(sanitize_started)
        diagnostics["pdf_sanitized"] = True
        diagnostics["removed_embedded_files"] = sanitized.removed_embedded_files
        diagnostics["removed_links"] = sanitized.removed_links

        inspect_started = time.perf_counter()
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            page_count = document.page_count
        finally:
            document.close()
        diagnostics["inspect_ms"] = elapsed_ms(inspect_started)
        diagnostics["page_count"] = page_count

        geometry_started = time.perf_counter()
        geometry = extract_text_geometry(
            pdf_bytes,
            progress_callback=on_progress,
            progress_label="Checking source text",
            progress_stage=Stage.SOURCE_CHECK,
        )
        diagnostics["preflight_geometry_ms"] = elapsed_ms(geometry_started)
        initial_summary = summarize_geometry_quality(geometry)
        diagnostics.update(
            {
                "initial_characters": initial_summary["total_characters"],
                "initial_non_whitespace_characters": initial_summary[
                    "non_whitespace_characters"
                ],
                "initial_alphanumeric_ratio": initial_summary["alphanumeric_ratio"],
                "initial_garbled_page_count": len(initial_summary["garbled_page_numbers"]),
                "initial_garbled_page_numbers": list(initial_summary["garbled_page_numbers"]),
            }
        )

        selected_pages = set(select_pages_requiring_ocr(pdf_bytes, initial_summary))
        hidden_pages = find_unstrippable_hidden_text_pages(pdf_bytes)
        duplicate_pages = find_coincident_text_layer_pages(pdf_bytes)
        selected_pages.update(hidden_pages)
        selected_pages.update(duplicate_pages)
        direct_page_numbers = sorted(selected_pages)
        diagnostics["direct_ocr_page_count"] = len(direct_page_numbers)
        diagnostics["direct_ocr_page_numbers"] = direct_page_numbers
        diagnostics["native_pages_reused"] = page_count - len(direct_page_numbers)
        diagnostics["forced_page_count"] = len(set(hidden_pages) | set(duplicate_pages))
        diagnostics["forced_page_numbers"] = sorted(set(hidden_pages) | set(duplicate_pages))

        if direct_page_numbers:
            diagnostics["mode"] = "direct"
            diagnostics["selected_profile"] = "direct-hocr"
            diagnostics["ocr_engine"] = active_ocr_engine()
            diagnostics["rasterizer"] = "pymupdf"
            direct_started = time.perf_counter()
            direct_pages, direct_stats, direct_diagnostics = _run_direct_ocr(
                pdf_bytes,
                direct_page_numbers,
                on_progress,
            )
            diagnostics["primary_ocr_ms"] = elapsed_ms(direct_started)
            diagnostics.update(direct_diagnostics)
            # _run_direct_ocr includes its retry ladder in the surrounding
            # elapsed time; adaptive_ocr_ms is a diagnostic subset, not an
            # additional duration.
            diagnostics["ocr_ms"] = diagnostics["primary_ocr_ms"]
            confidences = [
                float(stats["mean_confidence"])
                for stats in direct_stats.values()
                if int(stats["word_count"]) > 0
            ]
            diagnostics["direct_mean_confidence"] = (
                round(sum(confidences) / len(confidences), 2) if confidences else 0.0
            )
            geometry = merge_geometry_pages(geometry, direct_pages)
            direct_summary = summarize_geometry_quality(geometry)
            unresolved = sorted(
                set(direct_page_numbers) & set(direct_summary["garbled_page_numbers"])
            )
            if unresolved:
                raise RuntimeError(
                    "Direct OCR remained garbled on page(s) "
                    + ",".join(map(str, unresolved))
                )
        else:
            on_progress("Source text geometry is trustworthy; skipping OCR…", Stage.SOURCE)

        recovery_started = time.perf_counter()
        try:
            recovered_geometry, recovery = recover_table_geometry(
                pdf_bytes,
                geometry,
                progress_callback=on_progress,
            )
            diagnostics.update(
                {
                    key: recovery[key]
                    for key in (
                        "date_tables_detected",
                        "date_cells_detected",
                        "date_cells_resolved",
                        "date_cells_unresolved",
                        "table_cells_detected",
                        "table_cells_resolved",
                        "table_cells_unresolved",
                        "table_images_examined",
                        "table_images_skipped_small",
                        "table_images_skipped_non_document",
                        "table_grid_candidates",
                        "page_text_regions_detected",
                        "page_text_regions_failed",
                        "page_text_words_resolved",
                    )
                }
            )
            if recovery["changed"]:
                geometry = recovered_geometry
                diagnostics["ocr_engine"] = active_ocr_engine()
                diagnostics["rasterizer"] = "pymupdf"
                if diagnostics["mode"] == "none":
                    diagnostics["mode"] = "direct-table"
                    diagnostics["selected_profile"] = "table-cell"
                on_progress(
                    f"Recovered {recovery['table_cells_resolved']} of "
                    f"{recovery['table_cells_detected']} ruled-table cells",
                    Stage.TABLE_RECOVERY,
                )
        except Exception as exc:  # noqa: BLE001 — optional recovery must be safe
            diagnostics["table_text_recovery_error"] = str(exc)
            diagnostics["date_recovery_error"] = str(exc)
            on_progress("Table-cell recovery unavailable; keeping direct geometry…", Stage.TABLE_RECOVERY)
        diagnostics["table_text_recovery_ms"] = elapsed_ms(recovery_started)
        diagnostics["date_recovery_ms"] = diagnostics["table_text_recovery_ms"]

        final_summary = summarize_geometry_quality(geometry)
        diagnostics.update(
            {
                "final_characters": final_summary["total_characters"],
                "final_non_whitespace_characters": final_summary[
                    "non_whitespace_characters"
                ],
                "final_alphanumeric_ratio": final_summary["alphanumeric_ratio"],
                "final_garbled_page_count": len(final_summary["garbled_page_numbers"]),
                "final_garbled_page_numbers": list(final_summary["garbled_page_numbers"]),
                "quality_warning": needs_adaptive_retry(final_summary)
                or needs_garbled_text_retry(final_summary),
            }
        )

        table_structure_base64 = ""
        table_structure: dict | None = None
        table_started = time.perf_counter()
        try:
            on_progress("Detecting table structure…", Stage.TABLE_STRUCTURE)
            table_diagnostics: dict = {}
            table_structure = detect_tables(
                pdf_bytes,
                geometry,
                progress_callback=on_progress,
                diagnostics=table_diagnostics,
            )
            diagnostics.update(table_diagnostics)
            table_structure_base64 = structure_to_base64(table_structure)
        except Exception as exc:  # noqa: BLE001 — optional stage must preserve OCR
            diagnostics["table_structure_error"] = str(exc)
            on_progress("Table structure detection unavailable; keeping OCR geometry…", Stage.TABLE_STRUCTURE)
        diagnostics["table_structure_ms"] = elapsed_ms(table_started)

        # The two tiers read one prepared document. The financial tier runs
        # first because the value tier honours the claims it makes: a note
        # heading fences the integer printed inside it, and a citation is
        # published as a reference rather than read for values.
        prepared = None
        prepare_started = time.perf_counter()
        try:
            prepared = prepare_lines(geometry)
        except Exception as exc:  # noqa: BLE001 — optional stage must preserve OCR
            diagnostics["values_error"] = str(exc)
        diagnostics["lines_ms"] = elapsed_ms(prepare_started)

        financial_structure_base64 = ""
        financial_model: dict | None = None
        claims: tuple = ()
        structure_started = time.perf_counter()
        if prepared is not None:
            try:
                on_progress("Reading financial structure…", Stage.FINANCIAL_STRUCTURE)
                # Table structure is optional input, not a prerequisite: it
                # corroborates the contents rows item detection reads, and item
                # detection falls back to text geometry when it is absent.
                structure = detect_financial_structure(prepared, tables=table_structure)
                claims = structure.spans
                model = structure.model
                financial_model = model
                diagnostics["financial_structure_detector_version"] = model["detectorVersion"]
                diagnostics["financial_document_class"] = model["documentClass"]
                diagnostics["financial_notes_found"] = model["apparatus"]["notes"]["found"]
                diagnostics["financial_items_found"] = model["apparatus"]["items"]["found"]
                diagnostics["financial_note_references"] = len(model["noteReferences"])
                diagnostics["financial_item_references"] = len(model["itemReferences"])
                diagnostics["financial_items_from_contents"] = sum(
                    1 for item in model["items"] if item["tocEntries"]
                )
                # A document with no apparatus at all carries no artifact: there
                # is nothing for financial analysis to read, and every scanned invoice
                # would otherwise pay to carry an empty one.
                if model["documentClass"] != "neither":
                    financial_structure_base64 = financial_structure_to_base64(model)
            except Exception as exc:  # noqa: BLE001 — optional stage must preserve OCR
                diagnostics["financial_structure_error"] = str(exc)
                on_progress("Financial structure detection unavailable; keeping OCR geometry…", Stage.FINANCIAL_STRUCTURE)
        diagnostics["financial_structure_ms"] = elapsed_ms(structure_started)

        document_values_base64 = ""
        values_model: dict | None = None
        values_started = time.perf_counter()
        if prepared is not None:
            try:
                on_progress("Detecting values…", Stage.VALUES)
                values = detect_values(prepared, claims=claims, diagnostics=diagnostics)
                values_model = values
                document_values_base64 = values_to_base64(values)
                counts = {"number": 0, "percent": 0, "date": 0}
                for page in values["pages"]:
                    for value in page["values"]:
                        counts[value["kind"]] += 1
                diagnostics["value_detector_version"] = values["detectorVersion"]
                diagnostics["values_detected"] = sum(counts.values())
                diagnostics["value_numbers"] = counts["number"]
                diagnostics["value_percents"] = counts["percent"]
                diagnostics["value_dates"] = counts["date"]
                diagnostics.setdefault("value_references", 0)
                diagnostics.setdefault("value_structure", 0)
                diagnostics.setdefault("value_noise", 0)
            except Exception as exc:  # noqa: BLE001 — optional stage must preserve OCR
                diagnostics["values_error"] = str(exc)
                on_progress("Value detection unavailable; keeping OCR geometry…", Stage.VALUES)
        diagnostics["values_ms"] = elapsed_ms(values_started)

        # Reconcile runs last and only when the job asked for it. It is a scan
        # the user chose to run, not a stage of the cache build, and it reads
        # the values model this job just produced. Like every optional stage it
        # must not cost the caller its OCR result if it fails.
        reconcile_base64 = ""
        if job.analysis:
            try:
                reconcile = detect_reconcile(
                    pdf_bytes,
                    geometry,
                    document_id=job.document_id,
                    values=values_model,
                    financial=financial_model,
                    tables=table_structure,
                    progress_callback=on_progress,
                    diagnostics=diagnostics,
                )
                reconcile_base64 = reconcile_to_base64(reconcile)
            except Exception as exc:  # noqa: BLE001 — optional stage must preserve OCR
                diagnostics["reconcile_error"] = str(exc)
                on_progress("Reconcile scan unavailable; keeping OCR result…", Stage.RECONCILE)

        geometry_encode_started = time.perf_counter()
        geometry_base64 = geometry_to_base64(geometry)
        diagnostics["geometry_encode_ms"] = elapsed_ms(geometry_encode_started)

        pdf_base64 = ""
        if job.mode == "full":
            pdf_encode_started = time.perf_counter()
            pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
            diagnostics["pdf_encode_ms"] = elapsed_ms(pdf_encode_started)
            diagnostics["output_bytes"] = len(pdf_bytes)
        else:
            diagnostics["pdf_encode_ms"] = 0
            diagnostics["output_bytes"] = 0

        diagnostics["geometry_ms"] = (
            diagnostics.get("preflight_geometry_ms", 0)
            + diagnostics.get("table_text_recovery_ms", 0)
        )
        diagnostics["total_ms"] = elapsed_ms(started_at)
        on_progress("Transferring OCR result…", Stage.RESULT_TRANSFER)
        _write(
            OcrResult(
                job_id=job.job_id,
                status="success",
                pdf_base64=pdf_base64,
                geometry_base64=geometry_base64,
                table_structure_base64=table_structure_base64,
                document_values_base64=document_values_base64,
                financial_structure_base64=financial_structure_base64,
                reconcile_base64=reconcile_base64,
                diagnostics=diagnostics,
            ).to_dict()
        )
    except Exception as exc:  # noqa: BLE001
        diagnostics["page_count"] = page_count
        diagnostics["total_ms"] = elapsed_ms(started_at)
        _write(
            OcrResult(
                job_id=job.job_id,
                status="error",
                error=str(exc),
                diagnostics=diagnostics,
            ).to_dict()
        )


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            command = data.get("command", "ocr")
            if command == "convert":
                convert_job = ConvertJob.from_dict(data)
            else:
                ocr_job = OcrJob.from_dict(data)
        except (json.JSONDecodeError, KeyError, AttributeError) as exc:
            _write({"job_id": "", "status": "error", "error": f"Invalid job: {exc}"})
            continue

        if command == "convert":
            _handle_convert_job(convert_job)
        else:
            _handle_job(ocr_job)


if __name__ == "__main__":
    main()
