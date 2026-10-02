/**
 * Ordinals that number something rather than measure it: list markers, footnote
 * markers and the indicators that point at them. Mirrors Python
 * `engines/values/lists.py`. The chain thresholds come from the shared config.
 */
import type { Fragment, TextLine } from "./lines.ts";
import {
  BAND_REACH,
  COLUMN_TOLERANCE,
  INDICATOR_RATIO,
  INLINE_REACH,
  MIN_CHAIN,
  MIN_INDICATORS,
  MIN_PROSE_WORDS,
  REFERENCE_REACH,
  REPRESENTATIVE_LINES,
} from "./config.ts";
import { FOOTNOTE_MARKER, FOOTNOTE_REFERENCE, LIST_MARKER } from "./categories.ts";
import { median } from "./util.ts";

// ``3.1``, ``10.15``, ``1.``, ``(7)``, ``10.5†``.
const MARKER_RE = /(?<open>\()?(?<ordinal>\d{1,3}(?:\.\d{1,3})*)(?<close>[).])?(?<mark>[*†‡§¶]{0,2})(?=[\s(]|$)/y;

const WORD_RE = /[^\W\d_]{2,}/g;

// An ordinal that keeps going into a quantity was never a marker.
const CONTINUES_RE = /\s*(?:%|percent\b|\d|thousand|million|billion|trillion)/iy;

const ENUMERATOR_AFTER = new Set([...")]”’;:"]);
const INDICATOR_AFTER = new Set([...")]”’"]);

export interface Marker {
  fragment: Fragment;
  text: string;
  reason: string;
}

export interface ListDetection {
  markers: Marker[];
}

interface Candidate {
  lineIndex: number;
  pageIndex: number;
  start: number;
  end: number;
  text: string;
  ordinal: number[];
  left: number;
  top: number;
  parenthesised: boolean;
}

function parseCandidate(lineIndex: number, line: TextLine, at: number): Candidate | null {
  MARKER_RE.lastIndex = at;
  const match = MARKER_RE.exec(line.text);
  if (match === null) return null;
  const open = match.groups?.open ?? "";
  const close = match.groups?.close ?? "";
  // A bracket has to close, and a closing bracket has to have opened.
  if (Boolean(open) !== (close === ")")) return null;
  CONTINUES_RE.lastIndex = match.index + match[0].length;
  if (CONTINUES_RE.exec(line.text) !== null) return null;
  const ordinalText = match.groups?.ordinal ?? "";
  return {
    lineIndex,
    pageIndex: line.pageIndex,
    start: at,
    end: match.index + match[0].length,
    text: match[0],
    ordinal: ordinalText.split(".").map(Number),
    left: line.left,
    top: line.top,
    parenthesised: Boolean(open),
  };
}

function typicalHeights(lines: TextLine[]): Map<number, number> {
  const byPage = new Map<number, number[]>();
  for (const line of lines) {
    if (line.medianHeight > 0) {
      const list = byPage.get(line.pageIndex) ?? [];
      list.push(line.medianHeight);
      byPage.set(line.pageIndex, list);
    }
  }
  const everything = [...byPage.values()].flat();
  const document = everything.length > 0 ? median(everything) : 0;
  const result = new Map<number, number>();
  for (const [page, heights] of byPage) {
    result.set(page, heights.length >= REPRESENTATIVE_LINES ? median(heights) : document);
  }
  return result;
}

function isProse(text: string): boolean {
  return (text.match(WORD_RE) ?? []).length >= MIN_PROSE_WORDS;
}

function leadsProse(lines: TextLine[], index: number, rest: string): boolean {
  if (rest.trim()) return isProse(rest);
  const line = lines[index]!;
  for (let i = Math.max(0, index - BAND_REACH); i < Math.min(lines.length, index + BAND_REACH + 1); i++) {
    const other = lines[i]!;
    if (other === line || other.pageIndex !== line.pageIndex) continue;
    if (other.top >= line.bottom || other.bottom <= line.top) continue;
    if (other.left > line.right + line.medianWidth && isProse(other.text)) return true;
  }
  return false;
}

interface CandidateSets {
  leading: Candidate[];
  inline: Candidate[];
  trailing: Candidate[];
  raised: Candidate[];
}

function candidates(lines: TextLine[]): CandidateSets {
  const leading: Candidate[] = [];
  const inline: Candidate[] = [];
  const trailing: Candidate[] = [];
  const raised: Candidate[] = [];
  const typical = typicalHeights(lines);

  const INLINE_POSITION_RE = /(?<=\S)\s?(?=\(?\d)/g;

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index]!;
    const text = line.text;
    const head = text.length - text.trimStart().length;
    const leadingCandidate = parseCandidate(index, line, head);
    if (leadingCandidate !== null) {
      if (leadsProse(lines, index, text.slice(leadingCandidate.end))) {
        leading.push(leadingCandidate);
      } else if (
        !text.slice(leadingCandidate.end).trim()
        && line.medianHeight > 0
        && line.medianHeight < (typical.get(line.pageIndex) ?? 0) * INDICATOR_RATIO
      ) {
        raised.push(leadingCandidate);
      }
    }

    INLINE_POSITION_RE.lastIndex = 0;
    let opening: RegExpExecArray | null;
    while ((opening = INLINE_POSITION_RE.exec(text)) !== null) {
      // This pattern can match at zero width (the whitespace is optional), and
      // JavaScript's exec does not advance lastIndex past an empty match.
      if (opening[0].length === 0) INLINE_POSITION_RE.lastIndex++;
      const at = opening.index + opening[0].length;
      if (at <= head) continue;
      const candidate = parseCandidate(index, line, at);
      if (candidate === null) continue;
      const before = text.slice(0, at).trimEnd().replace(/[.,]+$/, "").at(-1) ?? "";
      const rest = text.slice(candidate.end).trim();
      MARKER_RE.lastIndex = 0;
      const restIsMarker = MARKER_RE.exec(rest) !== null;
      if ((!rest || restIsMarker) && (isAlpha(before) || INDICATOR_AFTER.has(before))) {
        trailing.push(candidate);
      } else if (isProse(rest) && (isAlpha(before) || ENUMERATOR_AFTER.has(before))) {
        inline.push(candidate);
      }
    }
  }

  return { leading, inline, trailing, raised };
}

const isAlpha = (c: string): boolean => /[A-Za-z]/.test(c);

function ordinalCompare(a: number[], b: number[]): number {
  const length = Math.min(a.length, b.length);
  for (let i = 0; i < length; i++) {
    if (a[i] !== b[i]) return a[i]! - b[i]!;
  }
  return a.length - b.length;
}

/** Whether `second` is the ordinal a list prints after `first`. */
function follows(first: number[], second: number[]): boolean {
  if (ordinalCompare(second, first) <= 0) return false;
  if (first.length === second.length && first.slice(0, -1).every((v, i) => v === second[i])) {
    return second[second.length - 1] === first[first.length - 1]! + 1;
  }
  return second[second.length - 1] === 1;
}

function chains(
  items: Candidate[],
  adjacent: (a: Candidate, b: Candidate) => boolean,
): Candidate[][] {
  const found: Candidate[][] = [];
  let current: Candidate[] = [];
  const sorted = [...items].sort((a, b) => (a.lineIndex - b.lineIndex) || (a.start - b.start));
  for (const candidate of sorted) {
    if (current.length > 0 && !(follows(current[current.length - 1]!.ordinal, candidate.ordinal) && adjacent(current[current.length - 1]!, candidate))) {
      if (current.length >= MIN_CHAIN) found.push(current);
      current = [];
    }
    current.push(candidate);
  }
  if (current.length >= MIN_CHAIN) found.push(current);
  return found;
}

export function detectLists(lines: TextLine[]): ListDetection {
  const { leading, inline, trailing, raised } = candidates(lines);
  const indicators = [...trailing, ...raised];
  const markers: Marker[] = [];
  const footnotes: Array<[Set<string>, number, number]> = [];

  function sameColumn(first: Candidate, second: Candidate): boolean {
    const tolerance = Math.max(COLUMN_TOLERANCE, lines[second.lineIndex]!.medianWidth * 3);
    return Math.abs(first.left - second.left) <= tolerance
      && second.pageIndex - first.pageIndex >= 0
      && second.pageIndex - first.pageIndex <= 1;
  }

  function samePassage(first: Candidate, second: Candidate): boolean {
    return first.pageIndex === second.pageIndex
      && second.lineIndex - first.lineIndex <= INLINE_REACH;
  }

  const chained = new Set<string>();
  for (const parenthesised of [false, true]) {
    const column = leading.filter((item) => item.parenthesised === parenthesised);
    for (const chain of chains(column, sameColumn)) {
      const reason = parenthesised ? FOOTNOTE_MARKER : LIST_MARKER;
      for (const item of chain) {
        markers.push({ fragment: { lineIndex: item.lineIndex, start: item.start, end: item.end }, text: item.text, reason });
        chained.add(`${item.lineIndex}:${item.start}`);
      }
      if (parenthesised) {
        footnotes.push([
          new Set(chain.map((item) => item.text)),
          chain[0]!.pageIndex,
          chain[chain.length - 1]!.pageIndex,
        ]);
      }
    }
  }

  // A note with no siblings, established by the marks pointing at it instead.
  for (const definition of leading) {
    if (!definition.parenthesised) continue;
    if (chained.has(`${definition.lineIndex}:${definition.start}`)) continue;
    let pointing = 0;
    for (const indicator of indicators) {
      if (
        indicator.text === definition.text
        && indicator.pageIndex === definition.pageIndex
        && indicator.top < definition.top
      ) {
        pointing++;
      }
    }
    if (pointing < MIN_INDICATORS) continue;
    markers.push({ fragment: { lineIndex: definition.lineIndex, start: definition.start, end: definition.end }, text: definition.text, reason: FOOTNOTE_MARKER });
    footnotes.push([new Set([definition.text]), definition.pageIndex, definition.pageIndex]);
  }

  for (const chain of chains(inline, samePassage)) {
    for (const item of chain) {
      markers.push({ fragment: { lineIndex: item.lineIndex, start: item.start, end: item.end }, text: item.text, reason: LIST_MARKER });
    }
  }

  const claimed = new Set(markers.map((marker) => `${marker.fragment.lineIndex}:${marker.fragment.start}`));
  for (const candidate of indicators) {
    if (claimed.has(`${candidate.lineIndex}:${candidate.start}`)) continue;
    if (footnotes.some(([forms, first, last]) =>
      forms.has(candidate.text) && first - REFERENCE_REACH <= candidate.pageIndex && candidate.pageIndex <= last,
    )) {
      markers.push({ fragment: { lineIndex: candidate.lineIndex, start: candidate.start, end: candidate.end }, text: candidate.text, reason: FOOTNOTE_REFERENCE });
    }
  }

  return { markers };
}
