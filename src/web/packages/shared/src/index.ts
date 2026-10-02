export type { NormalizedRect, SearchMatch } from "./types.js";
export type { TextGeometry, TextGeometryPage, TextGeometryCharacter } from "./geometry-decoder.js";
export { decodeTextGeometry } from "./geometry-decoder.js";
export type {
  DetectedTable,
  PageTables,
  TableBounds,
  TableColumn,
  TableRow,
  TableHeader,
  TableHeaderGroup,
  TablePeriod,
  TableStructure,
  TableTextLine,
} from "./table-structure-decoder.js";
export { decodeTableStructure, parseTableStructure } from "./table-structure-decoder.js";
export type {
  DetectedReference,
  DetectedStructure,
  DetectedValue,
  DocumentValues,
  NoiseKind,
  NoiseReason,
  NoiseSpan,
  PageValues,
  ReferenceKind,
  SpanBounds,
  StructureKind,
  SpanSegment,
  ValueContext,
  ValueKind,
} from "./document-values-decoder.js";
export {
  NOISE_REASONS,
  REFERENCE_KINDS,
  STRUCTURE_KINDS,
  decodeDocumentValues,
  parseDocumentValues,
} from "./document-values-decoder.js";
export type {
  FinancialApparatus,
  FinancialDocumentClass,
  FinancialHeading,
  FinancialItem,
  FinancialItemReference,
  FinancialItemTocEntry,
  FinancialNote,
  FinancialNoteReference,
  FinancialPresence,
  FinancialStructure,
} from "./financial-structure-decoder.js";
export { decodeFinancialStructure, parseFinancialStructure } from "./financial-structure-decoder.js";
export type { SpanTipContext } from "./span-describe.js";
export {
  describeItemHeader,
  describeItemReference,
  describeItemTocEntry,
  describeNoise,
  describeNoteHeader,
  describeNoteReference,
  describeReference,
  describeStructure,
  describeValue,
} from "./span-describe.js";
export type { HoverTipContent, HoverTipOptions } from "./hover-tip.js";
export { HoverTip } from "./hover-tip.js";
export type { CharacterEntry } from "./char-entries.js";
export type { SearchPageIndex, SearchPageOptions } from "./text-searcher.js";
export { buildCharEntriesFromGeometry, charEntriesToGeometry } from "./char-entries.js";
export {
  buildSearchPageIndex,
  buildSearchPageIndexFromEntries,
  cleanAutoInsertedSearchQuery,
  normalizeLinkerQuery,
  normalizeSearchQuery,
  pageTextMatchesQuery,
  searchPage,
  searchPageWithIndex,
} from "./text-searcher.js";
export { extractText } from "./text-extractor.js";
export { normalizeExtractedZeroPlaceholder } from "./zero-placeholder.js";
export { isTextEntryTarget } from "./text-entry-target.js";
export type { StageDefinition } from "./progress-stages.js";
export { STAGES, STAGE_ORDER, fileProgressFraction, stageLabel } from "./progress-stages.js";
export type { ModalAction, ModalActionVariant, ModalOptions } from "./modal.js";
export { Modal } from "./modal.js";
export { PdfViewer, applyNormalizedRectToElement, ensureOverlayLayer } from "./pdf-viewer.js";
export {
  encodeTextGeometry,
  extractTextGeometryFromPdfBase64,
  extractTextGeometryFromPdfDocument,
  extractTextGeometryFromPdfUrl,
} from "./pdf-text-geometry.js";
export { detectValues } from "./value-recognition/detector.ts";
export { recognizeSpans } from "./value-recognition/spans.ts";
