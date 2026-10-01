import type { CharacterEntry } from "./char-entries.js";
import type { NormalizedRect } from "./types.js";
import { normalizeExtractedZeroPlaceholder } from "./zero-placeholder.js";

const MIN_CHAR_BOX_OVERLAP = 0.3;
const SAME_LINE_OVERLAP = 0.7;
const SCRIPT_LINE_OVERLAP = 0.25;
const SCRIPT_HEIGHT_RATIO = 0.85;
const SCRIPT_MAX_WIDTH_IN_CHARS = 3;

interface ExtractionLine {
  entries: CharacterEntry[];
  sourceOrder: number;
  left: number;
  top: number;
  right: number;
  bottom: number;
  medianCharWidth: number;
  medianCharHeight: number;
}

interface OrderedEntry {
  entry: CharacterEntry;
  visualLine: number;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 === 0
    ? (ordered[middle - 1]! + ordered[middle]!) / 2
    : ordered[middle]!;
}

function summarizeLine(entries: CharacterEntry[], sourceOrder: number): ExtractionLine {
  const visible = entries.filter((entry) => entry.char.trim().length > 0);
  const measured = visible.length > 0 ? visible : entries;
  return {
    entries,
    sourceOrder,
    left: Math.min(...measured.map((entry) => entry.normLeft)),
    top: Math.min(...measured.map((entry) => entry.normTop)),
    right: Math.max(...measured.map((entry) => entry.normRight)),
    bottom: Math.max(...measured.map((entry) => entry.normBottom)),
    medianCharWidth: median(measured.map((entry) => entry.normRight - entry.normLeft)),
    medianCharHeight: median(measured.map((entry) => entry.normBottom - entry.normTop)),
  };
}

function verticalOverlapRatio(first: ExtractionLine, second: ExtractionLine): number {
  const overlap = Math.max(0, Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top));
  const smallerHeight = Math.min(first.bottom - first.top, second.bottom - second.top);
  return smallerHeight > 0 ? overlap / smallerHeight : 0;
}

function horizontalGap(first: ExtractionLine, second: ExtractionLine): number {
  if (first.right < second.left) return second.left - first.right;
  if (second.right < first.left) return first.left - second.right;
  return 0;
}

function mergeLines(first: ExtractionLine, second: ExtractionLine): ExtractionLine {
  return summarizeLine(
    first.entries.concat(second.entries),
    Math.min(first.sourceOrder, second.sourceOrder),
  );
}

function isInlineScript(script: ExtractionLine, base: ExtractionLine): boolean {
  if (script.medianCharHeight <= 0 || base.medianCharHeight <= 0) return false;
  if (script.medianCharHeight >= base.medianCharHeight * SCRIPT_HEIGHT_RATIO) return false;
  if (verticalOverlapRatio(script, base) < SCRIPT_LINE_OVERLAP) return false;

  const scriptWidth = script.right - script.left;
  const maxScriptWidth = Math.max(
    base.medianCharWidth * SCRIPT_MAX_WIDTH_IN_CHARS,
    (base.right - base.left) * 0.35,
  );
  if (scriptWidth > maxScriptWidth) return false;

  const adjacentDistance = Math.max(base.medianCharWidth, script.medianCharWidth) * 2;
  return horizontalGap(script, base) <= adjacentDistance;
}

/**
 * Reconstructs visual reading order inside a selection.
 *
 * PDF producers commonly emit a raised trademark/copyright sign, exponent, or
 * lowered subscript as a separate source line. That fragment can occur before
 * its base text in the content stream even though it sits to the right. Merge
 * only small, narrow, vertically-overlapping fragments into the neighboring
 * base line, then use horizontal geometry to order the characters.
 */
function orderForExtraction(entries: CharacterEntry[]): OrderedEntry[] {
  const bySourceLine = new Map<number, CharacterEntry[]>();
  for (const entry of entries) {
    const line = bySourceLine.get(entry.lineIndex);
    if (line) line.push(entry);
    else bySourceLine.set(entry.lineIndex, [entry]);
  }

  let lines = Array.from(bySourceLine.values(), (lineEntries, sourceOrder) => (
    summarizeLine(lineEntries, sourceOrder)
  ));

  // A visual line can be split into several ordinary-sized PDF text items.
  // Collapse those first so a script fragment has one stable base candidate.
  for (let index = 0; index < lines.length; index++) {
    for (let candidate = index + 1; candidate < lines.length;) {
      if (verticalOverlapRatio(lines[index]!, lines[candidate]!) >= SAME_LINE_OVERLAP) {
        lines[index] = mergeLines(lines[index]!, lines[candidate]!);
        lines.splice(candidate, 1);
      } else {
        candidate++;
      }
    }
  }

  // Attach each small raised/lowered fragment to its closest plausible base.
  // Processing smallest-first prevents one annotation from absorbing another
  // and becoming large enough to masquerade as the base line.
  const scripts = [...lines].sort((a, b) => a.medianCharHeight - b.medianCharHeight);
  for (const script of scripts) {
    const scriptIndex = lines.indexOf(script);
    if (scriptIndex === -1) continue;

    let bestIndex = -1;
    let bestScore = Number.POSITIVE_INFINITY;
    for (let index = 0; index < lines.length; index++) {
      if (index === scriptIndex) continue;
      const base = lines[index]!;
      if (!isInlineScript(script, base)) continue;

      const score = horizontalGap(script, base) - verticalOverlapRatio(script, base);
      if (score < bestScore) {
        bestScore = score;
        bestIndex = index;
      }
    }

    if (bestIndex === -1) continue;
    const base = lines[bestIndex]!;
    const merged = summarizeLine(base.entries.concat(script.entries), base.sourceOrder);
    const removeIndex = Math.max(bestIndex, scriptIndex);
    const replaceIndex = Math.min(bestIndex, scriptIndex);
    lines[replaceIndex] = merged;
    lines.splice(removeIndex, 1);
  }

  return lines
    .sort((a, b) => a.sourceOrder - b.sourceOrder)
    .flatMap((line, visualLine) => line.entries
      .sort((a, b) => a.normLeft - b.normLeft || a.normTop - b.normTop || a.itemIndex - b.itemIndex)
      .map((entry) => ({ entry, visualLine })));
}

function charBoxOverlapsRect(
  aLeft: number,
  aTop: number,
  aRight: number,
  aBottom: number,
  rect: NormalizedRect,
): boolean {
  const rectRight  = rect.x + rect.width;
  const rectBottom = rect.y + rect.height;

  const intersectLeft   = Math.max(aLeft, rect.x);
  const intersectTop    = Math.max(aTop, rect.y);
  const intersectRight  = Math.min(aRight, rectRight);
  const intersectBottom = Math.min(aBottom, rectBottom);

  if (intersectLeft >= intersectRight || intersectTop >= intersectBottom) return false;

  const charArea = (aRight - aLeft) * (aBottom - aTop);
  if (charArea <= 0) return false;

  const intersectArea = (intersectRight - intersectLeft) * (intersectBottom - intersectTop);
  return intersectArea / charArea >= MIN_CHAR_BOX_OVERLAP;
}

/**
 * Whether a whitespace glyph is mostly buried inside a printing glyph on its
 * own visual line.
 *
 * A right-aligned column often carries a run of space glyphs whose last member
 * reaches the column's text and overlaps its first glyph. That space is
 * alignment padding, not a separator: emitting it puts a space inside the figure
 * ("3 ,016,601"), and a number reader then splits it into two addends. Ordinary
 * word spaces may graze a neighbour's advance box but are never mostly covered
 * by it, so a majority overlap tells the two apart.
 */
function isPaddingOverlap(ordered: OrderedEntry[], index: number): boolean {
  const target = ordered[index]!.entry;
  const targetWidth = target.normRight - target.normLeft;
  if (targetWidth <= 0) return false;
  const visualLine = ordered[index]!.visualLine;
  for (const direction of [-1, 1]) {
    for (let i = index + direction; i >= 0 && i < ordered.length; i += direction) {
      const neighbor = ordered[i]!;
      if (neighbor.visualLine !== visualLine) break;
      const glyph = neighbor.entry;
      if (glyph.char.trim() === "") continue;
      const overlapWidth = Math.min(target.normRight, glyph.normRight)
        - Math.max(target.normLeft, glyph.normLeft);
      if (overlapWidth > targetWidth * 0.5) return true;
      break;
    }
  }
  return false;
}

export function extractText(entries: CharacterEntry[] | null, rect: NormalizedRect): string {
  if (!entries || entries.length === 0) return "";

  const included: CharacterEntry[] = [];
  for (const entry of entries) {
    if (charBoxOverlapsRect(entry.normLeft, entry.normTop, entry.normRight, entry.normBottom, rect)) {
      included.push(entry);
    }
  }

  if (included.length === 0) return "";

  const ordered = orderForExtraction(included);
  const spacesPrecomputed = entries[0]?.spacesPrecomputed === true;
  let result = "";
  let prev: OrderedEntry | null = null;

  for (let index = 0; index < ordered.length; index++) {
    const current = ordered[index]!;
    const entry = current.entry;
    if (entry.char.trim() === "" && isPaddingOverlap(ordered, index)) {
      continue;
    }
    if (prev !== null && current.visualLine !== prev.visualLine) {
      result += " ";
    } else if (!spacesPrecomputed && prev !== null && entry.itemIndex !== prev.entry.itemIndex) {
      const prevCharWidth = prev.entry.normRight - prev.entry.normLeft;
      const gap = entry.normLeft - prev.entry.normRight;
      if (gap > prevCharWidth) result += " ";
    }
    result += entry.char;
    prev = current;
  }

  return normalizeExtractedZeroPlaceholder(result);
}
