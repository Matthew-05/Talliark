/**
 * Content-addressed span identity, mirroring Python `engines/values/ids.py`.
 *
 * An id is a hash over what the span is and where it sits, never over the order
 * it was found in, so a detector change that inserts a span does not renumber
 * everything after it. The Python engine hashes with SHA-256; this port uses
 * `crypto.subtle` (asynchronous), which is why detection is async. The format is
 * the same `val|ref|str|noi-<16 hex>` the contract pins.
 */
import { NOISE, REFERENCE, STRUCTURE, VALUE } from "./categories.ts";
import type { SpanBounds } from "../document-values-decoder.js";

const PREFIX: Record<string, string> = {
  [VALUE]: "val",
  [REFERENCE]: "ref",
  [STRUCTURE]: "str",
  [NOISE]: "noi",
};

const encoder = new TextEncoder();

async function sha256Hex(key: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(key));
  const bytes = new Uint8Array(digest);
  let out = "";
  for (const byte of bytes) out += byte.toString(16).padStart(2, "0");
  return out;
}

/**
 * The id for one span, from its category, page, text and place. Bounds are
 * rounded to three decimals, and `occurrence` disambiguates the physically
 * impossible case of two identical spans resolving to one key.
 */
export async function spanId(
  category: string,
  pageIndex: number,
  text: string,
  bounds: SpanBounds,
  occurrence = 0,
): Promise<string> {
  const key = [
    PREFIX[category],
    String(pageIndex),
    text.trim().split(/\s+/).join(" "),
    bounds.x.toFixed(3),
    bounds.y.toFixed(3),
    bounds.width.toFixed(3),
    bounds.height.toFixed(3),
    String(occurrence),
  ].join("|");
  const digest = await sha256Hex(key);
  return `${PREFIX[category]}-${digest.slice(0, 16)}`;
}

/** Hands out span ids, resolving the collision case deterministically. */
export class SpanIds {
  private readonly taken = new Set<string>();

  async assign(
    category: string,
    pageIndex: number,
    text: string,
    bounds: SpanBounds,
  ): Promise<string> {
    let occurrence = 0;
    while (true) {
      const identifier = await spanId(category, pageIndex, text, bounds, occurrence);
      if (!this.taken.has(identifier)) {
        this.taken.add(identifier);
        return identifier;
      }
      occurrence++;
    }
  }
}
