/**
 * Build the document-values-v1 model from text geometry, mirroring Python
 * `engines/values/detector.py`. Four categories, one decision each.
 *
 * The frontend recognizer runs only when OCR has not, so it never receives the
 * financial tier's claims (`engines/financial` is OCR-only). The claims path is
 * therefore omitted; the model is complete without it, and structure spans come
 * solely from ordinal list/footnote markers.
 */
import type {
  DetectedValue,
  DocumentValues,
  SpanBounds,
} from "../document-values-decoder.js";
import type { TextGeometry } from "../geometry-decoder.js";
import {
  NOISE,
  REFERENCE,
  STRUCTURE,
  TOKEN_SHAPE_CATEGORY,
  VALUE,
  isClickable,
} from "./categories.ts";
import { classify, referenceKind } from "./evidence.ts";
import { SpanIds } from "./ids.ts";
import {
  boundsFor,
  isIsolatedFragment,
  lineEnvelope,
  overlapsRanges,
  prepare,
  spanHeight,
} from "./lines.ts";
import type { PreparedDocument, PreparedLine } from "./lines.ts";
import { detectLists } from "./lists.ts";
import {
  recognizeMagnitudePrefix,
  recognizeSpans,
  recognizeWrappedDate,
  scaleCanonicalDecimal,
  wrappedDateHeads,
  wrappedDateYears,
} from "./spans.ts";
import type { RecognizedSpan, RejectedToken } from "./spans.ts";
import { median } from "./util.ts";

const DETECTOR_VERSION = "document-values-frontend-1";

function valuePayload(
  span: RecognizedSpan,
  bounds: SpanBounds,
  identifier: string,
  inheritedCurrency: string,
): DetectedValue {
  const value: DetectedValue = {
    id: identifier,
    kind: span.kind,
    text: span.text,
    bounds,
    clickable: isClickable(VALUE, span.kind),
    confidence: span.confidence,
  };
  if (span.normalizedValue) value.normalizedValue = span.normalizedValue;
  if (span.magnitude) value.magnitude = span.magnitude as NonNullable<DetectedValue["magnitude"]>;
  const currency = span.currency || (span.kind === "number" ? inheritedCurrency : "");
  if (currency) value.currency = currency;
  if (span.datePrecision) value.datePrecision = span.datePrecision as NonNullable<DetectedValue["datePrecision"]>;
  if (span.dateOrder) value.dateOrder = span.dateOrder as NonNullable<DetectedValue["dateOrder"]>;
  return value;
}

function endsLine(span: RecognizedSpan, text: string): boolean {
  return text.slice(span.end).trim() === "";
}

interface WrappedDate {
  start: number;
  end: number;
  span: RecognizedSpan;
  bounds: SpanBounds;
  segments: Array<{ pageIndex: number; text: string; bounds: SpanBounds }>;
}

function wrappedDates(lines: PreparedLine[]): {
  values: Map<number, WrappedDate[]>;
  occupied: Map<number, Array<[number, number]>>;
} {
  const values = new Map<number, WrappedDate[]>();
  const occupied = new Map<number, Array<[number, number]>>();
  for (let lineIndex = 0; lineIndex < lines.length - 1; lineIndex++) {
    const line = lines[lineIndex]!;
    const following = lines[lineIndex + 1]!;
    if (line.pageIndex !== following.pageIndex) continue;
    const heads = wrappedDateHeads(line.text);
    const years = wrappedDateYears(following.text);
    if (heads.length === 0 || years.length === 0) continue;
    const currentEnvelope = lineEnvelope(line.source);
    const nextEnvelope = lineEnvelope(following.source);
    if (currentEnvelope === null || nextEnvelope === null) continue;
    const currentBottom = currentEnvelope[1];
    const currentHeight = currentEnvelope[2];
    const nextTop = nextEnvelope[0];
    const nextHeight = nextEnvelope[2];
    if (nextTop - currentBottom > Math.max(0.004, median([currentHeight, nextHeight]) * 0.75)) continue;

    const yearCandidates: Array<{ year: { start: number; end: number; text: string }; bounds: SpanBounds }> = [];
    for (const year of years) {
      if (!isIsolatedFragment(year.start, year.end, following.source)) continue;
      const yearBounds = boundsFor(year.start, year.end, following.source);
      if (yearBounds !== null) yearCandidates.push({ year, bounds: yearBounds });
    }
    const usedYears = new Set<number>();
    for (const head of heads) {
      const headBounds = boundsFor(head.start, head.end, line.source);
      if (headBounds === null) continue;
      const headCenter = headBounds.x + headBounds.width / 2;
      const padding = Math.max(0.006, headBounds.width * 0.15);
      const aligned: Array<{ index: number; candidate: { year: { start: number; end: number; text: string }; bounds: SpanBounds } }> = [];
      yearCandidates.forEach((candidate, index) => {
        if (usedYears.has(index)) return;
        const center = candidate.bounds.x + candidate.bounds.width / 2;
        if (headBounds.x - padding <= center && center <= headBounds.x + headBounds.width + padding) {
          aligned.push({ index, candidate });
        }
      });
      if (aligned.length === 0) continue;
      aligned.sort((a, b) =>
        Math.abs(b.candidate.bounds.x + b.candidate.bounds.width / 2 - headCenter)
        - Math.abs(a.candidate.bounds.x + a.candidate.bounds.width / 2 - headCenter));
      const chosen = aligned[aligned.length - 1]!;
      const { year, bounds: yearBounds } = chosen.candidate;
      const span = recognizeWrappedDate(head.text, year.text);
      if (span === null) continue;
      usedYears.add(chosen.index);
      const segments = [
        { pageIndex: line.pageIndex, text: head.text, bounds: headBounds },
        { pageIndex: line.pageIndex, text: year.text, bounds: yearBounds },
      ];
      const list = values.get(lineIndex) ?? [];
      list.push({ start: head.start, end: head.end, span, bounds: headBounds, segments });
      values.set(lineIndex, list);
      const occLine = occupied.get(lineIndex) ?? [];
      occLine.push([head.start, head.end]);
      occupied.set(lineIndex, occLine);
      const occNext = occupied.get(lineIndex + 1) ?? [];
      occNext.push([year.start, year.end]);
      occupied.set(lineIndex + 1, occNext);
    }
  }
  return { values, occupied };
}

function attachWrappedMagnitude(
  value: DetectedValue,
  span: RecognizedSpan,
  pageIndex: number,
  following: PreparedLine,
): boolean {
  if (span.kind !== "number" || span.magnitude) return false;
  const modifier = recognizeMagnitudePrefix(following.text);
  if (modifier === null) return false;
  const [modifierEnd, modifierText, magnitude] = modifier;
  const modifierBounds = boundsFor(0, modifierEnd, following.source);
  if (modifierBounds === null) return false;

  value.text = `${span.text.trimEnd()} ${modifierText}`;
  value.normalizedValue = scaleCanonicalDecimal(span.normalizedValue, magnitude);
  value.magnitude = magnitude as NonNullable<DetectedValue["magnitude"]>;
  value.segments = [
    { pageIndex, text: span.text, bounds: value.bounds },
    { pageIndex: following.pageIndex, text: modifierText, bounds: modifierBounds },
  ];
  return true;
}

export async function detectValues(geometry: TextGeometry): Promise<DocumentValues> {
  const document: PreparedDocument = prepare(geometry);
  const ids = new SpanIds();

  const byPage: Record<string, Map<number, unknown[]>> = {
    [VALUE]: new Map(),
    [REFERENCE]: new Map(),
    [STRUCTURE]: new Map(),
    [NOISE]: new Map(),
  };

  async function publish(
    pageIndex: number,
    category: string,
    kind: string,
    text: string,
    bounds: SpanBounds,
    label: string,
    identifier = "",
  ): Promise<void> {
    const id = identifier || await ids.assign(category, pageIndex, text, bounds);
    const payload: Record<string, unknown> = {
      id,
      kind: category === NOISE ? kind : label,
      text,
      bounds,
      clickable: isClickable(category, label),
    };
    if (category === NOISE) payload.reason = label;
    const page = byPage[category]!.get(pageIndex) ?? [];
    page.push(payload);
    byPage[category]!.set(pageIndex, page);
  }

  const occupiedByLine = new Map<number, Array<[number, number]>>();

  // Ordinal markers number a list rather than measuring anything, so they are
  // structure, and anything value-shaped inside one is refused too.
  for (const marker of detectLists(document.textLines).markers) {
    const lineIndex = marker.fragment.lineIndex;
    if (overlapsRanges(marker.fragment.start, marker.fragment.end, occupiedByLine.get(lineIndex) ?? [])) continue;
    const line = document.lines[lineIndex]!;
    const bounds = boundsFor(marker.fragment.start, marker.fragment.end, line.source);
    if (bounds === null) continue;
    await publish(line.pageIndex, STRUCTURE, "number", marker.text, bounds, marker.reason);
    const occ = occupiedByLine.get(lineIndex) ?? [];
    occ.push([marker.fragment.start, marker.fragment.end]);
    occupiedByLine.set(lineIndex, occ);
  }

  const { values: wrappedDateValues, occupied: wrappedOccupied } = wrappedDates(document.lines);

  for (let lineIndex = 0; lineIndex < document.lines.length; lineIndex++) {
    const line = document.lines[lineIndex]!;
    const pageIndex = line.pageIndex;
    const inheritedCurrency = line.context.currency ?? document.documentContext.currency ?? "";
    const fenced = occupiedByLine.get(lineIndex) ?? [];

    const candidates: Array<{
      start: number;
      end: number;
      span: RecognizedSpan;
      bounds: SpanBounds;
      segments: Array<{ pageIndex: number; text: string; bounds: SpanBounds }> | null;
    }> = (wrappedDateValues.get(lineIndex) ?? []).map((w) => ({
      start: w.start,
      end: w.end,
      span: w.span,
      bounds: w.bounds,
      segments: w.segments,
    }));

    const unparsed: RejectedToken[] = [];
    for (const span of recognizeSpans(line.text, unparsed)) {
      if (overlapsRanges(span.start, span.end, wrappedOccupied.get(lineIndex) ?? [])) continue;
      const bounds = boundsFor(span.start, span.end, line.source);
      if (bounds === null) continue;
      candidates.push({ start: span.start, end: span.end, span, bounds, segments: null });
    }

    for (const token of unparsed) {
      const bounds = boundsFor(token.start, token.end, line.source);
      if (bounds === null) continue;
      if (overlapsRanges(token.start, token.end, fenced)) continue;
      const [category, resolvedShape] = TOKEN_SHAPE_CATEGORY[token.reason] ?? [NOISE, "partial-token"];
      const resolved = category === REFERENCE
        ? referenceKind(line.text, token.start, token.end)
        : resolvedShape;
      await publish(pageIndex, category, "number", token.text, bounds, resolved);
    }

    candidates.sort((a, b) => a.start - b.start);
    for (const { start, end, span, bounds, segments } of candidates) {
      if (overlapsRanges(start, end, fenced)) continue;

      const modifierFollows = lineIndex + 1 < document.lines.length
        && span.kind === "number"
        && !span.magnitude
        && endsLine(span, line.text)
        && recognizeMagnitudePrefix(document.lines[lineIndex + 1]!.text) !== null;

      const [category, resolved] = classify(
        span,
        line.text,
        start,
        end,
        pageIndex,
        line.top,
        spanHeight(start, end, line.source),
        isIsolatedFragment(start, end, line.source),
        modifierFollows,
        document.profile,
      );

      if (category !== VALUE) {
        await publish(pageIndex, category, span.kind, span.text, bounds, resolved);
        continue;
      }

      const identifier = await ids.assign(VALUE, pageIndex, span.text, bounds);
      const value = valuePayload(span, bounds, identifier, inheritedCurrency);
      if (segments !== null) value.segments = segments;
      if (modifierFollows) {
        attachWrappedMagnitude(value, span, pageIndex, document.lines[lineIndex + 1]!);
      }
      const pageValues = byPage[VALUE]!.get(pageIndex) ?? [];
      pageValues.push(value);
      byPage[VALUE]!.set(pageIndex, pageValues);
    }
  }

  const pages = document.pageIndexes.map((pageIndex, index) => {
    const context = document.pageContexts[index] ?? {};
    const page: Record<string, unknown> = {
      pageIndex,
      context,
      values: byPage[VALUE]!.get(pageIndex) ?? [],
    };
    for (const [category, key] of [
      [REFERENCE, "references"],
      [STRUCTURE, "structure"],
      [NOISE, "noise"],
    ] as const) {
      const entries = byPage[category]!.get(pageIndex) ?? [];
      if (entries.length > 0) page[key] = entries;
    }
    return page as unknown as DocumentValues["pages"][number];
  });

  return {
    version: 1,
    coordinateSpace: "normalized",
    detectorVersion: DETECTOR_VERSION,
    documentContext: document.documentContext,
    pages,
  };
}
