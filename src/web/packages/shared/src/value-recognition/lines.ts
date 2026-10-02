/**
 * Turn text-geometry characters into the lines every detector reasons about.
 * Mirrors Python `engines/values/lines.py`. Pure geometry over text-geometry-v1:
 * no OCR or PDF APIs, and no opinion about what a number means.
 */
import type { TextGeometry } from "../geometry-decoder.js";
import type { SpanBounds, ValueContext } from "../document-values-decoder.js";
import { contextForText, documentContext } from "./context.ts";
import { buildDocumentProfile } from "./profile.ts";
import type { DocumentProfile } from "./profile.ts";
import { median } from "./util.ts";

export interface CharBox {
  char: string;
  x: number;
  y: number;
  width: number;
  height: number;
  lineIndex: number;
}

export interface Fragment {
  lineIndex: number;
  start: number;
  end: number;
}

export interface TextLine {
  pageIndex: number;
  text: string;
  left: number;
  right: number;
  top: number;
  bottom: number;
  medianWidth: number;
  medianHeight: number;
}

export interface PreparedLine {
  pageIndex: number;
  text: string;
  source: Array<CharBox | null>;
  context: ValueContext;
  top: number;
}

export interface PreparedDocument {
  pageIndexes: number[];
  lines: PreparedLine[];
  textLines: TextLine[];
  pageContexts: ValueContext[];
  documentContext: ValueContext;
  profile: DocumentProfile;
}

function lineCharacters(page: { characters: CharBox[] }): CharBox[][] {
  const byLine = new Map<number, CharBox[]>();
  for (const character of page.characters) {
    const index = character.lineIndex;
    const list = byLine.get(index) ?? [];
    list.push(character);
    byLine.set(index, list);
  }
  return [...byLine.entries()].sort((a, b) => a[0] - b[0]).map(([, chars]) => chars);
}

function lineText(characters: CharBox[]): [string, Array<CharBox | null>] {
  const ordered = [...characters].sort((a, b) => (a.x - b.x) || (a.y - b.y));
  const widths = ordered.filter((c) => c.width > 0).map((c) => c.width);
  const typical = widths.length > 0 ? median(widths) : 0.005;
  let output = "";
  const source: Array<CharBox | null> = [];
  let prior: CharBox | null = null;
  for (const character of ordered) {
    if (prior !== null) {
      const gap = character.x - (prior.x + prior.width);
      if (gap > typical * 1.25 && (output.length === 0 || output[output.length - 1]!.trim() !== "")) {
        output += " ";
        source.push(null);
      }
    }
    const value = character.char;
    for (const char of value) {
      output += char;
      source.push(character);
    }
    prior = character;
  }
  return [output, source];
}

function visible(source: Array<CharBox | null>): CharBox[] {
  return source.filter((item): item is CharBox => item !== null && item.char.trim() !== "");
}

export function lineEnvelope(source: Array<CharBox | null>): [number, number, number] | null {
  const characters = visible(source);
  if (characters.length === 0) return null;
  const top = Math.min(...characters.map((c) => c.y));
  const bottom = Math.max(...characters.map((c) => c.y + c.height));
  const heights = characters.filter((c) => c.height > 0).map((c) => c.height);
  return [top, bottom, heights.length > 0 ? median(heights) : Math.max(0.001, bottom - top)];
}

function lineLeft(source: Array<CharBox | null>): number {
  const chars = visible(source);
  return chars.length > 0 ? Math.min(...chars.map((c) => c.x)) : 0;
}

function lineRight(source: Array<CharBox | null>): number {
  const chars = visible(source);
  return chars.length > 0 ? Math.max(...chars.map((c) => c.x + c.width)) : 0;
}

function lineMedianWidth(source: Array<CharBox | null>): number {
  const widths = visible(source).filter((c) => c.width > 0).map((c) => c.width);
  return widths.length > 0 ? median(widths) : 0.005;
}

export function spanHeight(start: number, end: number, source: Array<CharBox | null>): number {
  const heights = visible(source.slice(start, end)).filter((c) => c.height > 0).map((c) => c.height);
  return heights.length > 0 ? median(heights) : 0;
}

export function boundsFor(start: number, end: number, source: Array<CharBox | null>): SpanBounds | null {
  const characters = visible(source.slice(start, end));
  if (characters.length === 0) return null;
  const left = Math.min(...characters.map((c) => c.x));
  const top = Math.min(...characters.map((c) => c.y));
  const right = Math.max(...characters.map((c) => c.x + c.width));
  const bottom = Math.max(...characters.map((c) => c.y + c.height));
  return {
    x: Math.max(0, Math.min(1, left)),
    y: Math.max(0, Math.min(1, top)),
    width: Math.max(0.000001, Math.min(1 - left, right - left)),
    height: Math.max(0.000001, Math.min(1 - top, bottom - top)),
  };
}

export function overlapsRanges(start: number, end: number, ranges: Array<[number, number]>): boolean {
  return ranges.some(([occupiedStart, occupiedEnd]) => start < occupiedEnd && end > occupiedStart);
}

export function isIsolatedFragment(start: number, end: number, source: Array<CharBox | null>): boolean {
  const characters = visible(source.slice(start, end));
  if (characters.length === 0) return false;
  const widths = characters.filter((c) => c.width > 0).map((c) => c.width);
  const typical = widths.length > 0 ? median(widths) : 0.005;
  const left = Math.min(...characters.map((c) => c.x));
  const right = Math.max(...characters.map((c) => c.x + c.width));
  const before = visible(source.slice(0, start)).at(-1);
  const after = visible(source.slice(end))[0];
  const leftGap = before === undefined ? Infinity : left - (before.x + before.width);
  const rightGap = after === undefined ? Infinity : after.x - right;
  return leftGap >= typical * 2 && rightGap >= typical * 2;
}

export function prepare(geometry: TextGeometry): PreparedDocument {
  const pageIndexes: number[] = [];
  const pageContexts: ValueContext[] = [];
  const perPage: Array<Array<[string, Array<CharBox | null>]>> = [];
  const glyphHeights = new Map<number, number[]>();

  for (const page of geometry.pages) {
    const chars = page.characters as CharBox[];
    const lines = lineCharacters(page).map((lineChars) => lineText(lineChars));
    pageIndexes.push(page.pageIndex);
    perPage.push(lines);
    pageContexts.push(contextForText(lines.map(([text]) => text).join("\n")));
    const heights = chars.filter((c) => c.height > 0).map((c) => c.height);
    glyphHeights.set(page.pageIndex, [...(glyphHeights.get(page.pageIndex) ?? []), ...heights]);
  }

  const prepared: PreparedLine[] = [];
  const textLines: TextLine[] = [];
  const profileLines: Array<{ pageIndex: number; text: string; top: number }> = [];
  for (let i = 0; i < pageIndexes.length; i++) {
    const pageIndex = pageIndexes[i]!;
    const pageContext = pageContexts[i]!;
    for (const [text, source] of perPage[i]!) {
      const envelope = lineEnvelope(source);
      const top = envelope !== null ? envelope[0] : 0;
      prepared.push({ pageIndex, text, source, context: pageContext, top });
      textLines.push({
        pageIndex,
        text,
        left: lineLeft(source),
        right: lineRight(source),
        top,
        bottom: envelope !== null ? envelope[1] : top,
        medianWidth: lineMedianWidth(source),
        medianHeight: envelope !== null ? envelope[2] : 0,
      });
      profileLines.push({ pageIndex, text, top });
    }
  }

  return {
    pageIndexes,
    lines: prepared,
    textLines,
    pageContexts,
    documentContext: documentContext(pageContexts),
    profile: buildDocumentProfile(profileLines, glyphHeights),
  };
}
