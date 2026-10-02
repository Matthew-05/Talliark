/**
 * The vocabulary for classifying a span, mirroring
 * `contracts/document-values-v1.json` and Python `engines/values/categories.py`.
 *
 * The category/kind/reason *strings* are structural identifiers used throughout
 * the recognizer; the closed enums (kinds, reasons) and the clickability policy
 * are read from `contracts/value-recognition-config-v1.json`, shared with the
 * Python engine.
 */
import { CLICKABLE_BY_DEFAULT, CLICKABLE_KINDS } from "./config.ts";

export const VALUE = "value";
export const REFERENCE = "reference";
export const STRUCTURE = "structure";
export const NOISE = "noise";

export const IDENTIFIER = "identifier";
export const PHONE = "phone";
export const POSTAL = "postal";
export const TAX_ID = "tax-id";
export const SECURITY_ID = "security-id";
export const NOTE = "note";
export const ITEM = "item";

export const NOTE_HEADER = "note-header";
export const ITEM_HEADER = "item-header";
export const ITEM_TOC_ENTRY = "item-toc-entry";
export const LIST_MARKER = "list-marker";
export const FOOTNOTE_MARKER = "footnote-marker";
export const FOOTNOTE_REFERENCE = "footnote-reference";

export const PARTIAL_TOKEN = "partial-token";
export const PAGE_FURNITURE = "page-furniture";
export const SUPERSCRIPT = "superscript";
export const CITATION_YEAR = "citation-year";
export const UNSUPPORTED = "unsupported";

export const ALPHANUMERIC = "alphanumeric";

/**
 * Whether a span becomes a click target. The policy lives in the shared config;
 * noise is forced off so a clickable span the detector could not identify can
 * never exist.
 */
export function isClickable(category: string, kind: string): boolean {
  if (category === NOISE) return false;
  return CLICKABLE_KINDS[kind] ?? CLICKABLE_BY_DEFAULT[category] ?? false;
}

/**
 * What a token that could not be read as a value becomes, from its shape alone.
 * The first two are printed data under a shape a value never takes, so they are
 * references; the third is damage and stays noise.
 */
export const TOKEN_SHAPE_CATEGORY: Record<string, [string, string]> = {
  [IDENTIFIER]: [REFERENCE, IDENTIFIER],
  [ALPHANUMERIC]: [REFERENCE, IDENTIFIER],
  [PARTIAL_TOKEN]: [NOISE, PARTIAL_TOKEN],
};
