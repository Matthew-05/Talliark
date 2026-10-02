/**
 * Recognize value spans (dates, numbers, percentages) without depending on OCR
 * or PDF libraries. Mirrors Python `engines/values/spans.py`.
 *
 * The dialect-specific regular expressions live here (Python and JavaScript
 * regexes differ: inline flag groups like `(?-i:...)`, and `re`'s named-group
 * syntax). The tables they are built from -- month names, currency codes, and
 * magnitude words -- come from `contracts/value-recognition-config-v1.json`.
 *
 * One deliberate deviation: Python writes the trailing currency code
 * case-sensitively with `(?-i:...)`, which JavaScript has no equivalent for.
 * Here the number pattern is case-insensitive, and a trailing code that came in
 * lowercase is refused afterwards, which reproduces the same result.
 */
import {
  CONFIDENCE,
  CURRENCY_CODE_PATTERN,
  CURRENCY_CODES,
  CURRENCY_SYMBOL_PATTERN,
  CURRENCY_SYMBOLS,
  MAGNITUDE_PATTERN,
  MAGNITUDES,
  MONTH_PATTERN,
  MONTHS,
} from "./config.ts";
import { ALPHANUMERIC, IDENTIFIER, PARTIAL_TOKEN } from "./categories.ts";

export interface RecognizedSpan {
  start: number;
  end: number;
  kind: "number" | "percent" | "date";
  text: string;
  confidence: number;
  normalizedValue: string;
  currency: string;
  datePrecision: string;
  dateOrder: string;
  magnitude: number;
}

export interface TextFragment {
  start: number;
  end: number;
  text: string;
}

export interface RejectedToken {
  start: number;
  end: number;
  text: string;
  reason: string;
}

// --- exact decimal arithmetic ---------------------------------------------
// The contract requires `normalizedValue` to be an exact canonical decimal,
// never a binary float, so the arithmetic below is string-based rather than
// going through `number`.

function canonicalDecimal(text: string, negative: boolean): string {
  const body = text.replace(/,/g, "");
  const match = /^(-?)(\d+(?:\.\d+)?|\.\d+)$/.exec(body);
  if (!match) return "";
  const textNegative = match[1] === "-";
  const unsigned = match[2]!;
  let rendered = decimalToFixed(unsigned);
  // Python writes `-abs(value)` when `negative` is set, and otherwise keeps the
  // text's own sign -- the multiplied result from `scaledDecimal` already carries
  // its sign, so `negative` is false there and the sign passes through here.
  if (negative || textNegative) rendered = "-" + rendered;
  if (rendered.includes(".")) {
    rendered = rendered.replace(/0+$/, "").replace(/\.$/, "");
  }
  return rendered || "0";
}

/** A plain decimal body to fixed-point, stripping leading zeros as Decimal does. */
function decimalToFixed(body: string): string {
  const parts = body.split(".");
  const integer = parts[0] === "" ? "0" : parts[0]!.replace(/^0+(?=\d)/, "");
  const fraction = parts[1];
  return fraction === undefined ? integer : integer + "." + fraction;
}

/** Multiply a canonical decimal by a power-of-ten magnitude (1000, 1e6, ...). */
function multiplyByPower10(canonical: string, magnitude: number): string {
  const shift = Math.round(Math.log10(magnitude));
  const negative = canonical.startsWith("-");
  const unsigned = negative ? canonical.slice(1) : canonical;
  const parts = unsigned.split(".");
  const integer = parts[0]!;
  const fraction = parts[1] ?? "";
  const combined = integer + fraction;
  const point = integer.length + shift;
  const result = point >= combined.length
    ? combined.padEnd(point, "0")
    : combined.slice(0, point) + "." + combined.slice(point);
  return (negative ? "-" : "") + result;
}

function scaledDecimal(text: string, negative: boolean, magnitude: number): string {
  const canonical = canonicalDecimal(text, negative);
  if (!canonical || !magnitude) return canonical;
  return canonicalDecimal(multiplyByPower10(canonical, magnitude), false);
}

/** Multiply an already-canonical decimal by a magnitude, mirroring the detector's helper. */
export function scaleCanonicalDecimal(value: string, magnitude: number): string {
  let scaled = multiplyByPower10(value, magnitude);
  if (scaled.includes(".")) scaled = scaled.replace(/0+$/, "").replace(/\.$/, "");
  return scaled || "0";
}

// --- months and date validation -------------------------------------------
function monthNumber(value: string): number | undefined {
  return MONTHS[value.replace(/\.+$/, "").toLowerCase()];
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

function validDate(year: number, month: number, day: number): boolean {
  if (month < 1 || month > 12) return false;
  return day >= 1 && day <= daysInMonth(year, month);
}

const pad2 = (n: number): string => String(n).padStart(2, "0");
const pad4 = (n: number): string => String(n).padStart(4, "0");

// --- compiled patterns -----------------------------------------------------
const MONTH = MONTH_PATTERN;

interface DatePattern {
  re: RegExp;
  mode: "mdy" | "dmy" | "ymd" | "numeric" | "month" | "quarter" | "year";
}

const DATE_PATTERNS: DatePattern[] = [
  { re: new RegExp(`\\b(?<month>${MONTH})\\.?\\s+(?<day>\\d{1,2})(?:st|nd|rd|th)?\\s*,?\\s+(?<year>(?:19|20)\\d{2})\\b`, "ig"), mode: "mdy" },
  { re: new RegExp(`\\b(?<day>\\d{1,2})(?:st|nd|rd|th)?\\s+(?<month>${MONTH})\\.?\\s*,?\\s+(?<year>(?:19|20)\\d{2})\\b`, "ig"), mode: "dmy" },
  { re: new RegExp(`\\b(?<year>(?:19|20)\\d{2})[-/.](?<month>0?[1-9]|1[0-2])[-/.](?<day>0?[1-9]|[12]\\d|3[01])\\b`, "g"), mode: "ymd" },
  { re: new RegExp(`(?<![\\d.])(?<a>0?[1-9]|[12]\\d|3[01])[/.-](?<b>0?[1-9]|[12]\\d|3[01])[/.-](?<year>(?:19|20)?\\d{2})(?![\\d.])`, "g"), mode: "numeric" },
  { re: new RegExp(`\\b(?<month>${MONTH})\\.?\\s+(?<year>(?:19|20)\\d{2})\\b`, "ig"), mode: "month" },
  { re: new RegExp(`\\b(?:FY\\s*)?(?<year>(?:19|20)\\d{2})\\s*(?:Q(?<q1>[1-4]))\\b|\\bQ(?<q2>[1-4])\\s*(?:FY\\s*)?(?<year2>(?:19|20)\\d{2})\\b`, "ig"), mode: "quarter" },
  { re: new RegExp(`\\b(?:FY\\s*)?(?<year>(?:19|20)\\d{2})\\b`, "ig"), mode: "year" },
];

const NUMBER_RE = new RegExp(
  `(?<![\\w\\d])` +
    `(?:(?<lead_code>${CURRENCY_CODE_PATTERN})\\s*|(?<lead_symbol>${CURRENCY_SYMBOL_PATTERN})\\s*)?` +
    `(?<open>\\()?\\s*` +
    `(?:(?<code>${CURRENCY_CODE_PATTERN})\\s*|(?<symbol>${CURRENCY_SYMBOL_PATTERN})\\s*)?` +
    `(?<sign>[+\\-\\u2212])?` +
    `(?<number>(?:\\d{1,3}(?:,\\d{3})+|\\d+)(?:\\.\\d+)?|\\.\\d+)` +
    `\\s*(?<close_percent>\\)(?=\\s*(?:%|percent\\b)))?` +
    `\\s*(?<percent>%|percent\\b)?\\s*` +
    `(?<magnitude>${MAGNITUDE_PATTERN})?\\s*` +
    `(?:(?<trail_code>${CURRENCY_CODE_PATTERN})\\b(?!-)\\s*)?` +
    `(?<close>\\))?` +
    `(?![\\w\\d])`,
  "gid",
);

const MAGNITUDE_PREFIX_RE = new RegExp(`^\\s*(?<magnitude>${MAGNITUDE_PATTERN})\\b`, "i");

const WRAPPED_MONTH_DAY_RE = new RegExp(
  `\\b(?:${MONTH})\\.?\\s+\\d{1,2}(?:st|nd|rd|th)?(?!\\s*,?\\s*(?:19|20)\\d{2}\\b)\\s*,?`,
  "gi",
);
const WRAPPED_YEAR_RE = /\b(?:19|20)\d{2}\b/g;

// A hyphen, slash or colon binding two alphanumeric runs together makes an
// identifier, not an arithmetic expression.
const JOIN_CHARACTERS = "-/:";

// Punctuation that may sit between a value and the edge of its token without
// being part of it.
const TRIMMABLE = new Set([...'.,;:!?"\'‘’“”()[]*†‡']);

const TOKEN_RE = /\S+/g;

const SYMBOL_CHARS = CURRENCY_SYMBOL_PATTERN.slice(1, -1);
const FIGURE_DASH_RE = new RegExp(`(?<=[\\d%)])[\\u2013\\u2014](?=[\\d(${SYMBOL_CHARS}])`, "g");

const REFERENCE_MARKS = "#№";
const MINUS = new Set(["-", "−"]);

// --- token shape -----------------------------------------------------------
const isAlpha = (c: string): boolean => /[A-Za-z]/.test(c);
const isDigitChar = (c: string): boolean => /[0-9]/.test(c);
const isAlnum = (c: string): boolean => /[A-Za-z0-9]/.test(c);

/** Why a token could never be a value, from its shape alone. */
export function tokenShape(token: string): string {
  for (const character of token) {
    if (REFERENCE_MARKS.includes(character)) return IDENTIFIER;
  }
  for (let index = 0; index < token.length; index++) {
    const character = token[index]!;
    if (
      JOIN_CHARACTERS.includes(character)
      && index > 0
      && isAlnum(token[index - 1]!)
      && index + 1 < token.length
      && isAlnum(token[index + 1]!)
    ) {
      return IDENTIFIER;
    }
  }
  if ([...token].some((c) => isAlpha(c)) && [...token].some((c) => isDigitChar(c))) {
    return ALPHANUMERIC;
  }
  return PARTIAL_TOKEN;
}

/** The units a value must align to: non-whitespace runs, split on figure dashes. */
export function tokenSpans(text: string): Array<[number, number]> {
  const spans: Array<[number, number]> = [];
  let tokenMatch: RegExpExecArray | null;
  TOKEN_RE.lastIndex = 0;
  while ((tokenMatch = TOKEN_RE.exec(text)) !== null) {
    let start = tokenMatch.index;
    FIGURE_DASH_RE.lastIndex = 0;
    let dash: RegExpExecArray | null;
    while ((dash = FIGURE_DASH_RE.exec(tokenMatch[0])) !== null) {
      spans.push([start, tokenMatch.index + dash.index]);
      start = tokenMatch.index + dash.index + dash[0].length;
    }
    spans.push([start, tokenMatch.index + tokenMatch[0].length]);
  }
  return spans;
}

/**
 * The first token this match cuts through, or null when it aligns. A value has
 * to claim whole tokens; "$1,234." aligns because only a full stop is left over.
 */
export function cutToken(
  text: string,
  tokens: Array<[number, number]>,
  start: number,
  end: number,
): [number, number] | null {
  for (const [tokenStart, tokenEnd] of tokens) {
    if (tokenEnd <= start || tokenStart >= end) continue;
    const outside =
      text.slice(tokenStart, Math.max(tokenStart, start))
      + text.slice(Math.min(tokenEnd, end), tokenEnd);
    if ([...outside].some((c) => !TRIMMABLE.has(c))) {
      return [tokenStart, tokenEnd];
    }
  }
  return null;
}

// --- date assembly ---------------------------------------------------------
function dateSpan(match: RegExpExecArray, mode: DatePattern["mode"]): RecognizedSpan | null {
  const g = match.groups as Record<string, string | undefined>;
  let normalized = "";
  let precision = "";
  let order = "";

  if (mode === "quarter") {
    const year = Number(g.year ?? g.year2 ?? 0);
    const quarter = Number(g.q1 ?? g.q2 ?? 0);
    normalized = `${pad4(year)}-Q${quarter}`;
    precision = "quarter";
    order = "ymd";
  } else if (mode === "year") {
    const year = Number(g.year);
    normalized = pad4(year);
    precision = "year";
    order = "ymd";
  } else if (mode === "month") {
    const year = Number(g.year);
    const month = monthNumber(g.month ?? "");
    if (month === undefined) return null;
    normalized = `${pad4(year)}-${pad2(month)}`;
    precision = "month";
    order = "mdy";
  } else if (mode === "numeric") {
    const first = Number(g.a);
    const second = Number(g.b);
    const yearText = g.year ?? "";
    let year = Number(yearText);
    if (yearText.length === 2) year += year < 70 ? 2000 : 1900;
    if (first <= 12 && second <= 12) {
      normalized = "";
      order = "ambiguous";
    } else if (first <= 12) {
      const month = first;
      const day = second;
      order = "mdy";
      if (!validDate(year, month, day)) return null;
      normalized = `${pad4(year)}-${pad2(month)}-${pad2(day)}`;
    } else {
      const day = first;
      const month = second;
      order = "dmy";
      if (!validDate(year, month, day)) return null;
      normalized = `${pad4(year)}-${pad2(month)}-${pad2(day)}`;
    }
    precision = "day";
  } else {
    const year = Number(g.year);
    const monthRaw = g.month ?? "";
    const month = /^\d+$/.test(monthRaw) ? Number(monthRaw) : monthNumber(monthRaw);
    const day = Number(g.day);
    if (month === undefined || !validDate(year, month, day)) return null;
    normalized = `${pad4(year)}-${pad2(month)}-${pad2(day)}`;
    precision = "day";
    order = mode;
  }

  return {
    start: match.index,
    end: match.index + match[0].length,
    kind: "date",
    text: match[0],
    confidence: precision === "day" ? CONFIDENCE.dateDay : CONFIDENCE.dateOther,
    normalizedValue: normalized,
    datePrecision: precision,
    dateOrder: order,
    currency: "",
    magnitude: 0,
  };
}

function overlaps(start: number, end: number, occupied: Array<[number, number]>): boolean {
  return occupied.some(([left, right]) => start < right && end > left);
}

function groupStart(match: RegExpExecArray, name: string): number {
  return match.indices?.groups?.[name]?.[0] ?? -1;
}

function groupEnd(match: RegExpExecArray, name: string): number {
  return match.indices?.groups?.[name]?.[1] ?? -1;
}

/** Return the first magnitude token when it begins the supplied line. */
export function recognizeMagnitudePrefix(text: string): [number, string, number] | null {
  const match = MAGNITUDE_PREFIX_RE.exec(text);
  if (match === null) return null;
  const modifier = match.groups?.magnitude ?? "";
  return [match.index + match[0].length, modifier, MAGNITUDES[modifier.toLowerCase()] ?? 0];
}

/** Month/day fragments that still need a year from the line below. */
export function wrappedDateHeads(text: string): TextFragment[] {
  const fragments: TextFragment[] = [];
  let match: RegExpExecArray | null;
  WRAPPED_MONTH_DAY_RE.lastIndex = 0;
  while ((match = WRAPPED_MONTH_DAY_RE.exec(text)) !== null) {
    fragments.push({ start: match.index, end: match.index + match[0].length, text: match[0].trim() });
  }
  return fragments;
}

/** Four-digit year fragments that can finish a wrapped month/day. */
export function wrappedDateYears(text: string): TextFragment[] {
  const fragments: TextFragment[] = [];
  let match: RegExpExecArray | null;
  WRAPPED_YEAR_RE.lastIndex = 0;
  while ((match = WRAPPED_YEAR_RE.exec(text)) !== null) {
    fragments.push({ start: match.index, end: match.index + match[0].length, text: match[0] });
  }
  return fragments;
}

/** Build the ordinary date span produced when two visual fragments are joined. */
export function recognizeWrappedDate(head: string, year: string): RecognizedSpan | null {
  const combined = `${head.trimEnd()} ${year.trim()}`;
  for (const span of recognizeSpans(combined)) {
    if (span.kind === "date" && span.datePrecision === "day") return span;
  }
  return null;
}

/**
 * Non-overlapping date/percent/number spans in source order. A span must align
 * to token boundaries; pass `rejected` to collect the tokens that held something
 * value-shaped and were refused for cutting across one.
 */
export function recognizeSpans(
  text: string,
  rejected?: RejectedToken[],
): RecognizedSpan[] {
  const results: RecognizedSpan[] = [];
  const occupied: Array<[number, number]> = [];
  const tokens = tokenSpans(text);
  const reported = new Set<string>();

  function cutsAToken(start: number, end: number): boolean {
    const token = cutToken(text, tokens, start, end);
    if (token === null) return false;
    if (rejected !== undefined) {
      const key = `${token[0]}:${token[1]}`;
      if (!reported.has(key)) {
        reported.add(key);
        const body = text.slice(token[0], token[1]);
        rejected.push({ start: token[0], end: token[1], text: body, reason: tokenShape(body) });
      }
    }
    occupied.push([start, end]);
    return true;
  }

  for (const { re, mode } of DATE_PATTERNS) {
    let match: RegExpExecArray | null;
    re.lastIndex = 0;
    while ((match = re.exec(text)) !== null) {
      if (overlaps(match.index, match.index + match[0].length, occupied)) continue;
      const span = dateSpan(match, mode);
      if (span === null) continue;
      if (cutsAToken(span.start, span.end)) continue;
      results.push(span);
      occupied.push([span.start, span.end]);
    }
  }

  let match: RegExpExecArray | null;
  NUMBER_RE.lastIndex = 0;
  while ((match = NUMBER_RE.exec(text)) !== null) {
    const g = match.groups as Record<string, string | undefined>;
    let start = match.index;
    let end = match.index + match[0].length;
    if (overlaps(start, end, occupied)) continue;

    const openParen = g.open ?? "";
    const closeParen = g.close ?? g.close_percent ?? "";
    if (Boolean(openParen) !== Boolean(closeParen)) {
      if (openParen) {
        const positions = [groupStart(match, "code"), groupStart(match, "symbol"), groupStart(match, "number")]
          .filter((p) => p >= 0);
        start = Math.min(...positions);
      } else {
        end = g.percent ? groupEnd(match, "percent") : groupEnd(match, "number");
      }
    }

    const numberText = g.number ?? "";
    const sign = g.sign ?? "";
    const negative = (sign === "-" || sign === "−") || (Boolean(openParen) && Boolean(closeParen));
    const magnitudeText = g.magnitude ?? "";
    let magnitude = MAGNITUDES[magnitudeText.toLowerCase()] ?? 0;
    let normalized = scaledDecimal(numberText, negative, magnitude);
    if (!normalized) continue;

    // The trailing code is case-sensitive in Python (via (?-i:...)); JavaScript
    // has no inline flag group, so a lowercase match is refused here instead.
    let trailCode = g.trail_code ?? "";
    if (trailCode && trailCode !== trailCode.toUpperCase()) {
      const trail = match.indices?.groups?.trail_code;
      if (trail) end = trail[0];
      trailCode = "";
    }

    const code = (g.code ?? g.lead_code ?? trailCode ?? "").toUpperCase();
    const symbol = g.symbol ?? g.lead_symbol ?? "";
    const currency = (CURRENCY_CODES[code] ?? "") || (CURRENCY_SYMBOLS[symbol] ?? "");
    const percent = Boolean(g.percent);
    if (percent && magnitude) {
      magnitude = 0;
      normalized = canonicalDecimal(numberText, negative);
      end = groupEnd(match, "percent");
    }

    if (cutsAToken(start, end)) continue;

    const confidence = percent || currency
      ? CONFIDENCE.numberPercentOrCurrency
      : numberText.includes(",")
        ? CONFIDENCE.numberComma
        : numberText.includes(".")
          ? CONFIDENCE.numberDecimal
          : CONFIDENCE.numberBare;

    results.push({
      start,
      end,
      kind: percent ? "percent" : "number",
      text: text.slice(start, end).trim(),
      confidence,
      normalizedValue: normalized,
      magnitude,
      currency,
      datePrecision: "",
      dateOrder: "",
    });
    occupied.push([start, end]);
  }

  return results.sort((a, b) => (a.start - b.start) || (a.end - b.end));
}
