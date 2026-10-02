/**
 * Infer conservative currency and magnitude context, mirroring Python
 * `engines/values/context.py`. Currency and scale are read off the page text;
 * the shared config supplies the codes, symbols and scale phrases.
 */
import { CURRENCY_CODES, CURRENCY_SYMBOLS, SCALE_BODIES } from "./config.ts";
import type { ValueContext } from "../document-values-decoder.js";

const SCALE_PATTERNS = SCALE_BODIES.map(({ scale, body }) => ({
  scale,
  pattern: new RegExp(`\\b${body}\\b`, "i"),
}));

function countOccurrences(text: string, needle: string): number {
  if (needle === "") return 0;
  return text.split(needle).length - 1;
}

/** Currency and scale a single page's text asserts for its figures. */
export function contextForText(text: string): ValueContext {
  const context: ValueContext = {};
  const upper = text.toUpperCase();

  const counts = new Map<string, number>();
  for (const code of Object.keys(CURRENCY_CODES)) {
    const matches = upper.match(new RegExp(`\\b${code}\\b`, "g")) ?? [];
    counts.set(code, (counts.get(code) ?? 0) + matches.length);
  }
  for (const [symbol, code] of Object.entries(CURRENCY_SYMBOLS)) {
    counts.set(code, (counts.get(code) ?? 0) + countOccurrences(text, symbol));
  }

  let currency = "";
  let best = 0;
  for (const [code, count] of counts) {
    if (count > best) {
      best = count;
      currency = code;
    }
  }
  if (best > 0) context.currency = currency;

  for (const { scale, pattern } of SCALE_PATTERNS) {
    if (pattern.test(text)) {
      context.scale = scale as NonNullable<ValueContext["scale"]>;
      break;
    }
  }
  return context;
}

/** The currency most pages agree on, read across the whole document. */
export function documentContext(pageContexts: ValueContext[]): ValueContext {
  const counts = new Map<string, number>();
  for (const context of pageContexts) {
    if (!context.currency) continue;
    counts.set(context.currency, (counts.get(context.currency) ?? 0) + 1);
  }
  let currency = "";
  let best = 0;
  for (const [code, count] of counts) {
    if (count > best) {
      best = count;
      currency = code;
    }
  }
  return best > 0 ? { currency } : {};
}
