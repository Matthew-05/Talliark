/**
 * The single source of truth for the value recognizer's vocabulary, policy,
 * tables and thresholds: `contracts/value-recognition-config-v1.json`, shared
 * with the Python OCR engine (`src/python/engines/values/config.py`). A change
 * to what values get captured is made there once and reflected in both runtimes.
 *
 * The dialect-specific regular expressions (number/date/marker grammars) stay
 * in their module; this file only carries what the two runtimes genuinely
 * share. The constants below mirror `config.py` so the two recognizers read
 * the same tables.
 */
import raw from "../../../../../../contracts/value-recognition-config-v1.json" with { type: "json" };

export interface IdentifierCue {
  kind: string;
  fragment: string;
}

export interface ScaleBody {
  scale: number;
  body: string;
}

export interface ValueRecognitionConfig {
  version: 1;
  categories: string[];
  referenceKinds: string[];
  structureKinds: string[];
  noiseReasons: string[];
  clickable: { byDefault: Record<string, boolean>; byKind: Record<string, boolean> };
  currencies: { codes: string[]; symbols: Record<string, string> };
  magnitudes: Record<string, number>;
  monthPattern: string;
  magnitudePattern: string;
  months: Record<string, number>;
  confidence: {
    dateDay: number;
    dateOther: number;
    numberPercentOrCurrency: number;
    numberComma: number;
    numberDecimal: number;
    numberBare: number;
  };
  evidence: {
    identifierCues: IdentifierCue[];
    numberMarkFragment: string;
    proseWords: number;
    periodContextFragment: string;
    citationContextFragment: string;
  };
  context: { scaleBodies: ScaleBody[] };
  profile: {
    superscriptRatio: number;
    representativeGlyphs: number;
    sequenceMinimum: number;
    yearRange: number[];
    placeTolerance: number;
    repeatShare: number;
    repeatMinimum: number;
    functionWords: string[];
  };
  lists: {
    minChain: number;
    columnTolerance: number;
    inlineReach: number;
    referenceReach: number;
    minIndicators: number;
    indicatorRatio: number;
    representativeLines: number;
    minProseWords: number;
    bandReach: number;
  };
}

export const config = raw as unknown as ValueRecognitionConfig;

// --- vocabulary (closed enums) ---------------------------------------------
export const CATEGORIES = config.categories as readonly string[];
export const REFERENCE_KINDS = config.referenceKinds as readonly string[];
export const STRUCTURE_KINDS = config.structureKinds as readonly string[];
export const NOISE_REASONS = config.noiseReasons as readonly string[];

export const CLICKABLE_BY_DEFAULT = config.clickable.byDefault;
export const CLICKABLE_KINDS = config.clickable.byKind;

// --- recognition tables -----------------------------------------------------
export const CURRENCY_CODES = Object.fromEntries(config.currencies.codes.map((code) => [code, code]));
export const CURRENCY_SYMBOLS = { ...config.currencies.symbols };
export const MAGNITUDES = { ...config.magnitudes };
export const MONTHS = { ...config.months };

export const MONTH_PATTERN = config.monthPattern;
export const MAGNITUDE_PATTERN = config.magnitudePattern;

export const CURRENCY_CODE_PATTERN = [...config.currencies.codes].sort().join("|");
export const CURRENCY_SYMBOL_PATTERN = "[" + Object.keys(config.currencies.symbols).join("") + "]";

// --- confidence -------------------------------------------------------------
export const CONFIDENCE = config.confidence;

// --- evidence word fragments ------------------------------------------------
export const IDENTIFIER_CUES = config.evidence.identifierCues;
export const NUMBER_MARK_FRAGMENT = config.evidence.numberMarkFragment;
export const PROSE_WORDS = config.evidence.proseWords;
export const PERIOD_CONTEXT_FRAGMENT = config.evidence.periodContextFragment;
export const CITATION_CONTEXT_FRAGMENT = config.evidence.citationContextFragment;

// --- context scale bodies ---------------------------------------------------
export const SCALE_BODIES = config.context.scaleBodies;

// --- profile thresholds -----------------------------------------------------
export const SUPERSCRIPT_RATIO = config.profile.superscriptRatio;
export const REPRESENTATIVE_GLYPHS = config.profile.representativeGlyphs;
export const SEQUENCE_MINIMUM = config.profile.sequenceMinimum;
export const YEAR_RANGE = config.profile.yearRange as unknown as readonly [number, number];
export const PLACE_TOLERANCE = config.profile.placeTolerance;
export const REPEAT_SHARE = config.profile.repeatShare;
export const REPEAT_MINIMUM = config.profile.repeatMinimum;
export const FUNCTION_WORDS = new Set(config.profile.functionWords);

// --- list thresholds --------------------------------------------------------
export const MIN_CHAIN = config.lists.minChain;
export const COLUMN_TOLERANCE = config.lists.columnTolerance;
export const INLINE_REACH = config.lists.inlineReach;
export const REFERENCE_REACH = config.lists.referenceReach;
export const MIN_INDICATORS = config.lists.minIndicators;
export const INDICATOR_RATIO = config.lists.indicatorRatio;
export const REPRESENTATIVE_LINES = config.lists.representativeLines;
export const MIN_PROSE_WORDS = config.lists.minProseWords;
export const BAND_REACH = config.lists.bandReach;
