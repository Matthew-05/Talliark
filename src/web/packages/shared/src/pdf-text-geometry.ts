import * as pdfjsLib from "pdfjs-dist";
import type { CharacterEntry } from "./char-entries.js";
import type { TextGeometry, TextGeometryCharacter } from "./geometry-decoder.js";

pdfjsLib.GlobalWorkerOptions.workerSrc ||= "pdf.worker.min.mjs";

interface PdfTextItem {
  str: string;
  transform: number[];
  width: number;
  height: number;
  fontName: string;
  hasEOL: boolean;
}

interface PdfTextStyle {
  fontFamily?: string;
  ascent?: number;
  descent?: number;
}

interface PdfViewportGeometry {
  width: number;
  height: number;
  transform: number[];
}

export interface PdfTextItemPlacement {
  normLeft: number;
  normTop: number;
  normRight: number;
  normBottom: number;
  inlineStartX: number;
  inlineEndX: number;
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

function isNewLine(prev: CharacterEntry, nextTop: number): boolean {
  const prevHeight = prev.normBottom - prev.normTop;
  const threshold = Math.max(prevHeight * 0.5, 0.001);
  return nextTop - prev.normTop > threshold;
}

function measureCharWidths(
  ctx: CanvasRenderingContext2D | null,
  chars: string[],
  fontSize: number,
  fontFamily: string,
): number[] {
  if (!ctx) return chars.map(() => 1);

  ctx.font = `${fontSize}px ${fontFamily}, sans-serif`;
  return chars.map((char) => ctx.measureText(char).width);
}

/** Resolve a PDF.js text item into the displayed page coordinate space. */
export function pdfTextItemPlacement(
  item: Pick<PdfTextItem, "transform" | "width" | "height">,
  viewport: PdfViewportGeometry,
  style?: PdfTextStyle,
): PdfTextItemPlacement {
  const transform = pdfjsLib.Util.transform(viewport.transform, item.transform);
  const rawInlineScale = Math.hypot(item.transform[0] ?? 0, item.transform[1] ?? 0);
  const displayInlineScale = Math.hypot(transform[0] ?? 0, transform[1] ?? 0);
  const inlineUnitX = displayInlineScale > 0 ? (transform[0] ?? 0) / displayInlineScale : 1;
  const inlineUnitY = displayInlineScale > 0 ? (transform[1] ?? 0) / displayInlineScale : 0;
  const inlineLength = item.width * (
    rawInlineScale > 0 ? displayInlineScale / rawInlineScale : 1
  );

  const rawBlockScale = Math.hypot(item.transform[2] ?? 0, item.transform[3] ?? 0);
  const displayBlockScale = Math.hypot(transform[2] ?? 0, transform[3] ?? 0);
  const blockUnitX = displayBlockScale > 0 ? (transform[2] ?? 0) / displayBlockScale : 0;
  const blockUnitY = displayBlockScale > 0 ? (transform[3] ?? 0) / displayBlockScale : -1;
  const emHeight = item.height * (
    rawBlockScale > 0 ? displayBlockScale / rawBlockScale : 1
  );
  const ascent = typeof style?.ascent === "number" && Number.isFinite(style.ascent)
    ? style.ascent
    : 1;
  const descent = typeof style?.descent === "number" && Number.isFinite(style.descent)
    ? style.descent
    : 0;

  const originX = transform[4] ?? 0;
  const originY = transform[5] ?? 0;
  const inlineEndX = originX + inlineUnitX * inlineLength;
  const inlineEndY = originY + inlineUnitY * inlineLength;
  const corners = [
    [originX + blockUnitX * ascent * emHeight, originY + blockUnitY * ascent * emHeight],
    [originX + blockUnitX * descent * emHeight, originY + blockUnitY * descent * emHeight],
    [inlineEndX + blockUnitX * ascent * emHeight, inlineEndY + blockUnitY * ascent * emHeight],
    [inlineEndX + blockUnitX * descent * emHeight, inlineEndY + blockUnitY * descent * emHeight],
  ];
  const xs = corners.map(([x]) => x ?? 0);
  const ys = corners.map(([, y]) => y ?? 0);

  return {
    normLeft: Math.min(...xs) / viewport.width,
    normTop: Math.min(...ys) / viewport.height,
    normRight: Math.max(...xs) / viewport.width,
    normBottom: Math.max(...ys) / viewport.height,
    inlineStartX: originX / viewport.width,
    inlineEndX: inlineEndX / viewport.width,
  };
}

async function buildPageEntries(page: pdfjsLib.PDFPageProxy): Promise<CharacterEntry[]> {
  const viewport = page.getViewport({ scale: 1 });
  const textContent = await page.getTextContent();
  const entries: CharacterEntry[] = [];
  let itemIndex = 0;
  let lineIndex = 0;
  let lastEntry: CharacterEntry | null = null;
  let lastItemEndedLine = false;

  const measureCtx = document.createElement("canvas").getContext("2d");

  for (const raw of textContent.items) {
    if (!("str" in raw)) {
      itemIndex++;
      continue;
    }

    const item = raw as PdfTextItem;
    if (!item.str) {
      if (item.hasEOL && lastEntry && !lastItemEndedLine) lineIndex++;
      lastItemEndedLine = item.hasEOL;
      itemIndex++;
      continue;
    }

    const chars = [...item.str];
    if (chars.length === 0) {
      itemIndex++;
      continue;
    }

    const style = textContent.styles[item.fontName];
    const placement = pdfTextItemPlacement(item, viewport, style);
    const { normLeft, normTop, normRight, normBottom } = placement;
    const fontFamily = style?.fontFamily ?? "sans-serif";
    const fontSize = item.height;

    if (lastEntry && !lastItemEndedLine && isNewLine(lastEntry, normTop)) {
      lineIndex++;
    }

    const charWidths = measureCharWidths(measureCtx, chars, fontSize, fontFamily);
    const totalMeasured = charWidths.reduce((sum, w) => sum + w, 0);
    const itemWidth = normRight - normLeft;
    const scale = totalMeasured > 0 ? itemWidth / totalMeasured : itemWidth / chars.length;

    let xOffset = 0;
    const reversed = placement.inlineEndX < placement.inlineStartX;
    for (let i = 0; i < chars.length; i++) {
      const charWidth = totalMeasured > 0 ? (charWidths[i] ?? 0) * scale : itemWidth / chars.length;
      const charRight = reversed ? normRight - xOffset : normLeft + xOffset + charWidth;
      const charLeft = reversed ? charRight - charWidth : normLeft + xOffset;
      const entry: CharacterEntry = {
        char: chars[i] ?? "",
        normLeft: charLeft,
        normTop,
        normRight: charRight,
        normBottom,
        lineIndex,
        itemIndex,
        spacesPrecomputed: false,
      };
      entries.push(entry);
      lastEntry = entry;
      xOffset += charWidth;
    }

    lastItemEndedLine = item.hasEOL;
    if (lastItemEndedLine) lineIndex++;

    itemIndex++;
  }

  return entries;
}

function entryToCharacter(entry: CharacterEntry): TextGeometryCharacter {
  return {
    char: entry.char,
    x: entry.normLeft,
    y: entry.normTop,
    width: entry.normRight - entry.normLeft,
    height: entry.normBottom - entry.normTop,
    lineIndex: entry.lineIndex,
  };
}

export async function extractTextGeometryFromPdfDocument(
  doc: pdfjsLib.PDFDocumentProxy,
): Promise<TextGeometry> {
  const pages: TextGeometry["pages"] = [];

  for (let pageNum = 1; pageNum <= doc.numPages; pageNum++) {
    const page = await doc.getPage(pageNum);
    try {
      const entries = await buildPageEntries(page);
      pages.push({
        pageIndex: pageNum - 1,
        characters: entries.map(entryToCharacter),
      });
    } finally {
      page.cleanup();
    }
  }

  return { version: 1, coordinateSpace: "normalized", pages };
}

export async function extractTextGeometryFromPdfUrl(url: string): Promise<TextGeometry> {
  const doc = await pdfjsLib.getDocument(url).promise;
  try {
    return await extractTextGeometryFromPdfDocument(doc);
  } finally {
    doc.destroy();
  }
}

export async function extractTextGeometryFromPdfBase64(base64: string): Promise<TextGeometry> {
  const doc = await pdfjsLib.getDocument({ data: base64ToBytes(base64) }).promise;
  try {
    return await extractTextGeometryFromPdfDocument(doc);
  } finally {
    doc.destroy();
  }
}

export async function encodeTextGeometry(geometry: TextGeometry): Promise<string> {
  const json = JSON.stringify(geometry);
  const stream = new Blob([json]).stream().pipeThrough(new CompressionStream("gzip"));
  const compressed = new Uint8Array(await new Response(stream).arrayBuffer());
  return bytesToBase64(compressed);
}
