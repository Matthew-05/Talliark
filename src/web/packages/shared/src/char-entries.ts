import type { TextGeometry } from "./geometry-decoder.js";

export interface CharacterEntry {
  char: string;
  normLeft: number;
  normTop: number;
  normRight: number;
  normBottom: number;
  lineIndex: number;
  itemIndex: number;
  spacesPrecomputed?: boolean;
}

interface EntryBounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface EntryLine {
  entries: CharacterEntry[];
  text: string;
  bounds: EntryBounds | null;
}

const DUPLICATE_LINE_OVERLAP = 0.8;

function lineText(entries: CharacterEntry[]): string {
  return entries.map((entry) => entry.char).join("").trim().replace(/\s+/g, " ");
}

function lineBounds(entries: CharacterEntry[]): EntryBounds | null {
  const visible = entries.filter((entry) => entry.char.trim() !== "");
  if (visible.length === 0) return null;
  return {
    left: Math.min(...visible.map((entry) => entry.normLeft)),
    top: Math.min(...visible.map((entry) => entry.normTop)),
    right: Math.max(...visible.map((entry) => entry.normRight)),
    bottom: Math.max(...visible.map((entry) => entry.normBottom)),
  };
}

function boundsAreCoincident(first: EntryBounds, second: EntryBounds): boolean {
  const intersectionWidth = Math.max(
    0,
    Math.min(first.right, second.right) - Math.max(first.left, second.left),
  );
  const intersectionHeight = Math.max(
    0,
    Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top),
  );
  const minimumWidth = Math.min(first.right - first.left, second.right - second.left);
  const minimumHeight = Math.min(
    first.bottom - first.top,
    second.bottom - second.top,
  );
  return minimumWidth > 0
    && minimumHeight > 0
    && intersectionWidth / minimumWidth >= DUPLICATE_LINE_OVERLAP
    && intersectionHeight / minimumHeight >= DUPLICATE_LINE_OVERLAP;
}

function deduplicateCoincidentLines(entries: CharacterEntry[]): CharacterEntry[] {
  const entriesByLine = new Map<number, CharacterEntry[]>();
  for (const entry of entries) {
    const line = entriesByLine.get(entry.lineIndex);
    if (line) line.push(entry);
    else entriesByLine.set(entry.lineIndex, [entry]);
  }

  const retained: EntryLine[] = [];
  const boundsByText = new Map<string, EntryBounds[]>();
  for (const lineEntries of entriesByLine.values()) {
    const text = lineText(lineEntries);
    const bounds = lineBounds(lineEntries);
    const duplicate = text !== "" && bounds !== null
      && (boundsByText.get(text) ?? []).some((prior) => boundsAreCoincident(bounds, prior));
    if (duplicate) continue;
    retained.push({ entries: lineEntries, text, bounds });
    if (text !== "" && bounds !== null) {
      const priorBounds = boundsByText.get(text);
      if (priorBounds) priorBounds.push(bounds);
      else boundsByText.set(text, [bounds]);
    }
  }

  return retained.flatMap((line, lineIndex) => line.entries.map((entry) => ({
    ...entry,
    lineIndex,
  })));
}

export function buildCharEntriesFromGeometry(
  geometry: TextGeometry,
): Map<number, CharacterEntry[]> {
  const pageMap = new Map<number, CharacterEntry[]>();

  for (const page of geometry.pages) {
    const entries: CharacterEntry[] = [];

    for (let index = 0; index < page.characters.length; index++) {
      const box = page.characters[index];
      if (box === undefined) continue;
      const entry: CharacterEntry = {
        char: box.char,
        normLeft: box.x,
        normTop: box.y,
        normRight: box.x + box.width,
        normBottom: box.y + box.height,
        lineIndex: box.lineIndex,
        itemIndex: index,
        spacesPrecomputed: true,
      };

      entries.push(entry);
    }

    pageMap.set(page.pageIndex, deduplicateCoincidentLines(entries));
  }

  return pageMap;
}

/**
 * The inverse of `buildCharEntriesFromGeometry`: a text-geometry model from the
 * cached character entries. The value recognizer reads text-geometry-v1, and the
 * viewer's cache already holds the characters, so this lets the recognizer run
 * over a document that was never OCR'd without re-extracting the text.
 */
export function charEntriesToGeometry(
  entriesByPage: Map<number, CharacterEntry[]>,
): TextGeometry {
  const pages: TextGeometry["pages"] = [];
  const ordered = [...entriesByPage.entries()].sort((a, b) => a[0] - b[0]);
  for (const [pageIndex, entries] of ordered) {
    pages.push({
      pageIndex,
      characters: entries.map((entry) => ({
        char: entry.char,
        x: entry.normLeft,
        y: entry.normTop,
        width: entry.normRight - entry.normLeft,
        height: entry.normBottom - entry.normTop,
        lineIndex: entry.lineIndex,
      })),
    });
  }
  return { version: 1, coordinateSpace: "normalized", pages };
}
