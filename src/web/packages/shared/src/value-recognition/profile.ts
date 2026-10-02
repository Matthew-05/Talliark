/**
 * Document-level facts no single line can establish on its own: running
 * headers/footers, page numbers, and the ordinary glyph height used to spot
 * superscripts. Mirrors Python `engines/values/profile.py`. The thresholds come
 * from `contracts/value-recognition-config-v1.json`.
 */
import {
  FUNCTION_WORDS,
  MONTHS,
  PLACE_TOLERANCE,
  REPEAT_MINIMUM,
  REPEAT_SHARE,
  REPRESENTATIVE_GLYPHS,
  SEQUENCE_MINIMUM,
  SUPERSCRIPT_RATIO,
  YEAR_RANGE,
} from "./config.ts";
import { PAGE_FURNITURE } from "./categories.ts";
import { median } from "./util.ts";

const DIGITS_RE = /\d+/g;
const WORDS_RE = /[^\W\d_]+/g;
// A line that is nothing but a small integer: the shape a bare page number takes.
const ONLY_NUMBER_RE = /^[(\[]?-?\s*(\d{1,4})\s*[)\]]?[.,]?$/;

/** The line with everything that may change from page to page masked out. */
function skeleton(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(" ").replace(DIGITS_RE, "#");
}

function slots(text: string): number[] {
  return (text.split(/\s+/).filter(Boolean).join(" ").match(/\d+/g) ?? []).map(Number);
}

/** Whether the skeleton still says anything specific. */
function namesItself(skeletonText: string): boolean {
  const words = skeletonText.match(WORDS_RE) ?? [];
  return words.some((word) => {
    if (word.length < 2) return false;
    const lower = word.toLowerCase();
    return !Object.hasOwn(MONTHS, lower) && !FUNCTION_WORDS.has(lower);
  });
}

function place(top: number): number {
  return Math.round(top * 2000);
}

interface ProfileLine {
  pageIndex: number;
  text: string;
  top: number;
}

export class DocumentProfile {
  labels: Map<number, Set<string>>;
  pageNumbers: Map<number, Set<string>>;
  glyphHeights: Map<number, number>;
  typicalGlyphHeight: number;

  constructor(
    labels: Map<number, Set<string>>,
    pageNumbers: Map<number, Set<string>>,
    glyphHeights: Map<number, number>,
    typicalGlyphHeight: number,
  ) {
    this.labels = labels;
    this.pageNumbers = pageNumbers;
    this.glyphHeights = glyphHeights;
    this.typicalGlyphHeight = typicalGlyphHeight;
  }

  furnitureReason(text: string, pageIndex: number, top: number): string {
    const key = skeleton(text);
    if (this.labels.get(pageIndex)?.has(key)) return PAGE_FURNITURE;
    const pageNumberKey = `${text.split(/\s+/).filter(Boolean).join(" ")}|${place(top)}`;
    if (this.pageNumbers.get(pageIndex)?.has(pageNumberKey)) return PAGE_FURNITURE;
    return "";
  }

  isSuperscript(pageIndex: number, height: number): boolean {
    const typical = this.glyphHeights.get(pageIndex) ?? this.typicalGlyphHeight;
    return height > 0 && typical > 0 && height < typical * SUPERSCRIPT_RATIO;
  }
}

const EMPTY_PROFILE = new DocumentProfile(
  new Map(),
  new Map(),
  new Map(),
  0,
);

/** Bare page numbers, found by the offset they share rather than their text. */
function pageNumbers(
  pageLines: Map<number, Array<[number, string]>>,
  pageCount: number,
): Map<number, Set<string>> {
  const votes = new Map<number, Array<[number, number, string]>>();
  for (const [page, entries] of pageLines) {
    for (const [top, text] of entries) {
      const match = ONLY_NUMBER_RE.exec(text);
      if (match === null) continue;
      const value = Number(match[1]);
      if (value >= YEAR_RANGE[0] && value <= YEAR_RANGE[1] && pageCount < YEAR_RANGE[0]) continue;
      const offset = value - page;
      const list = votes.get(offset) ?? [];
      list.push([page, top, text]);
      votes.set(offset, list);
    }
  }

  const found = new Map<number, Set<string>>();
  for (const [, entries] of votes) {
    const pages = [...new Set(entries.map(([page]) => page))].sort((a, b) => a - b);
    const runs: number[][] = [];
    for (const page of pages) {
      if (runs.length > 0 && page === runs[runs.length - 1]![runs[runs.length - 1]!.length - 1]! + 1) {
        runs[runs.length - 1]!.push(page);
      } else {
        runs.push([page]);
      }
    }
    for (const run of runs) {
      if (run.length < SEQUENCE_MINIMUM) continue;
      const runSet = new Set(run);
      const members = entries.filter(([page]) => runSet.has(page));
      const middle = median([...new Set(members.map(([, top]) => top))].sort((a, b) => a - b));
      const settled: Array<[number, number, string]> = [];
      for (const page of run) {
        const candidates = members.filter(([p]) => p === page);
        let best = candidates[0]!;
        for (const candidate of candidates) {
          if (Math.abs(candidate[1] - middle) < Math.abs(best[1] - middle)) best = candidate;
        }
        settled.push(best);
      }
      if (settled.some(([, top]) => Math.abs(place(top) - place(middle)) > PLACE_TOLERANCE)) continue;
      for (const page of run) {
        const candidates = members.filter(([p]) => p === page);
        let pageNumber = candidates[0]!;
        for (const candidate of candidates) {
          if (Math.abs(candidate[1] - middle) < Math.abs(pageNumber[1] - middle)) pageNumber = candidate;
        }
        const set = found.get(page) ?? new Set<string>();
        set.add(`${pageNumber[2]}|${place(pageNumber[1])}`);
        found.set(page, set);
      }
    }
  }
  return found;
}

interface ReducedLine {
  skeleton: string;
  slots: number[];
}

function reduce(pageLines: Map<number, string[]>): Map<number, ReducedLine[]> {
  const result = new Map<number, ReducedLine[]>();
  for (const [page, texts] of pageLines) {
    result.set(page, texts.map((text) => ({ skeleton: skeleton(text), slots: slots(text) })));
  }
  return result;
}

function repeatingSkeletons(pageLines: Map<number, ReducedLine[]>): Set<string> {
  const occurrences = new Map<string, Array<[number, number[]]>>();
  for (const [pageIndex, page] of pageLines) {
    for (const line of page) {
      const list = occurrences.get(line.skeleton) ?? [];
      list.push([pageIndex, line.slots]);
      occurrences.set(line.skeleton, list);
    }
  }

  const candidates = new Set<string>();
  for (const [key, rows] of occurrences) {
    const pages = rows.map(([page]) => page);
    if (pages.length !== new Set(pages).size) continue;
    const firstSlots = rows[0]?.[1] ?? [];
    const columns: number[][] = [];
    if (firstSlots.length > 0) {
      for (let i = 0; i < firstSlots.length; i++) {
        columns.push(rows.map(([, values]) => values[i] ?? 0));
      }
    }
    let tracksPage = false;
    let valid = true;
    for (const column of columns) {
      if (new Set(column).size === 1) continue;
      const offsets = new Set(column.map((value, index) => value - pages[index]!));
      if (offsets.size === 1) {
        tracksPage = true;
        continue;
      }
      valid = false;
      break;
    }
    if (valid && (tracksPage || namesItself(key))) {
      candidates.add(key);
    }
  }
  return candidates;
}

function edgeMatter(page: ReducedLine[], candidates: Set<string>): Set<string> {
  const ordinary: number[] = [];
  page.forEach((line, index) => {
    if (!candidates.has(line.skeleton)) ordinary.push(index);
  });
  if (ordinary.length === 0) return new Set(page.map((line) => line.skeleton));
  const first = ordinary[0]!;
  const last = ordinary[ordinary.length - 1]!;
  const result = new Set<string>();
  page.forEach((line, index) => {
    if ((index < first || index > last) && candidates.has(line.skeleton)) {
      result.add(line.skeleton);
    }
  });
  return result;
}

export function buildDocumentProfile(
  lines: ProfileLine[],
  glyphHeights: Map<number, number[]>,
): DocumentProfile {
  const pages = new Set<number>([...lines.map((l) => l.pageIndex), ...glyphHeights.keys()]);
  if (pages.size === 0) return EMPTY_PROFILE;

  const byPage = new Map<number, Array<[number, string]>>();
  for (const { pageIndex, text, top } of lines) {
    if (!text.trim()) continue;
    const list = byPage.get(pageIndex) ?? [];
    list.push([top, text.split(/\s+/).filter(Boolean).join(" ")]);
    byPage.set(pageIndex, list);
  }

  const ordered = new Map<number, string[]>();
  for (const [page, entries] of byPage) {
    entries.sort((a, b) => a[0] - b[0]);
    ordered.set(page, entries.map(([, text]) => text));
  }

  const numbers = pageNumbers(byPage, pages.size);
  const reduced = reduce(ordered);
  const candidates = repeatingSkeletons(reduced);
  const threshold = Math.max(REPEAT_MINIMUM, pages.size * REPEAT_SHARE);

  const pageCounts = new Map<string, number>();
  for (const page of reduced.values()) {
    const seen = new Set<string>();
    for (const line of page) {
      if (!seen.has(line.skeleton)) {
        seen.add(line.skeleton);
        pageCounts.set(line.skeleton, (pageCounts.get(line.skeleton) ?? 0) + 1);
      }
    }
  }
  const documentWide = new Set(
    [...candidates].filter((key) => (pageCounts.get(key) ?? 0) >= threshold),
  );

  const labels = new Map<number, Set<string>>();
  for (const [page, pageLines] of reduced) {
    const keys = edgeMatter(pageLines, documentWide);
    if (keys.size > 0) labels.set(page, keys);
  }

  const typical = new Map<number, number>();
  for (const [page, heights] of glyphHeights) {
    if (heights.length >= REPRESENTATIVE_GLYPHS) typical.set(page, median(heights));
  }
  const everything = [...glyphHeights.values()].flat();
  return new DocumentProfile(
    labels,
    numbers,
    typical,
    everything.length > 0 ? median(everything) : 0,
  );
}
