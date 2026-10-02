import type {
  DetectedReference,
  DetectedStructure,
  DetectedValue,
  DocumentValues,
  FinancialApparatus,
  FinancialDocumentClass,
  FinancialItem,
  FinancialItemReference,
  FinancialNote,
  FinancialNoteReference,
  FinancialStructure,
  NoiseSpan,
  TextGeometry,
  ValueContext,
} from "@talliark/shared";

const EMPTY_APPARATUS: FinancialApparatus = {
  notes: { searched: false, found: 0 },
  items: { searched: false, found: 0 },
  contents: { searched: false, found: 0 },
  parts: { searched: false, found: 0 },
};

/**
 * The two artifacts one cache build publishes, held per PDF.
 *
 * They arrive together and are read together: a citation's geometry lives in
 * the values model and what it resolves to lives in the structure model, joined
 * by span id. A document outside the financial tier carries no structure
 * artifact at all, which is not an error — `apparatus` is what says whether an
 * apparatus was looked for.
 */
export class ValuesCache {
  private readonly _values = new Map<string, Map<number, DetectedValue[]>>();
  private readonly _logicalValues = new Map<string, Map<number, DetectedValue[]>>();
  private readonly _references = new Map<string, Map<number, DetectedReference[]>>();
  private readonly _structure = new Map<string, Map<number, DetectedStructure[]>>();
  private readonly _noise = new Map<string, Map<number, NoiseSpan[]>>();
  private readonly _pageContexts = new Map<string, Map<number, ValueContext>>();
  private readonly _documentContexts = new Map<string, ValueContext>();
  private readonly _financialStructure = new Map<string, FinancialStructure | null>();
  private readonly _generations = new Map<string, number>();
  private _epoch = 0;
  private readonly _valuesDecoder: ((base64: string) => Promise<DocumentValues>) | undefined;
  private readonly _structureDecoder: ((base64: string) => Promise<FinancialStructure>) | undefined;
  private readonly _valuesRecognizer: ((geometry: TextGeometry) => Promise<DocumentValues>) | undefined;

  constructor(
    valuesDecoder?: (base64: string) => Promise<DocumentValues>,
    structureDecoder?: (base64: string) => Promise<FinancialStructure>,
    valuesRecognizer?: (geometry: TextGeometry) => Promise<DocumentValues>,
  ) {
    this._valuesDecoder = valuesDecoder;
    this._structureDecoder = structureDecoder;
    this._valuesRecognizer = valuesRecognizer;
  }

  /**
   * Build the value model for one PDF.
   *
   * OCR values always win: when `documentValuesBase64` is present the frontend
   * recognizer is never consulted. `fallbackGeometry` is the document's text
   * geometry, used to recognize values in the browser only when the document was
   * never OCR'd.
   */
  async build(
    pdfId: string,
    documentValuesBase64?: string,
    financialStructureBase64?: string,
    fallbackGeometry?: TextGeometry,
  ): Promise<void> {
    this.clearPdf(pdfId);
    this._reset(pdfId);
    const epoch = this._epoch;
    const generation = this._generations.get(pdfId) ?? 0;
    if (documentValuesBase64) {
      try {
        const decode = this._valuesDecoder ?? (await import("@talliark/shared")).decodeDocumentValues;
        const values = await decode(documentValuesBase64);
        if (this._isCurrent(pdfId, epoch, generation)) this._ingestValues(pdfId, values);
      } catch {
        // A malformed optional artifact must not prevent the PDF itself loading.
        if (this._isCurrent(pdfId, epoch, generation)) this._reset(pdfId);
      }
    } else if (fallbackGeometry) {
      try {
        const recognize = this._valuesRecognizer ?? (await import("@talliark/shared")).detectValues;
        const values = await recognize(fallbackGeometry);
        if (this._isCurrent(pdfId, epoch, generation)) this._ingestValues(pdfId, values);
      } catch {
        // Recognition is best-effort; a failure leaves OCR values (absent here)
        // as the only source, which is an empty model rather than an error.
        if (this._isCurrent(pdfId, epoch, generation)) this._reset(pdfId);
      }
    }
    if (financialStructureBase64) {
      try {
        const decode = this._structureDecoder ?? (await import("@talliark/shared")).decodeFinancialStructure;
        const structure = await decode(financialStructureBase64);
        if (this._isCurrent(pdfId, epoch, generation)) {
          this._financialStructure.set(pdfId, structure);
        }
      } catch {
        if (this._isCurrent(pdfId, epoch, generation)) {
          this._financialStructure.set(pdfId, null);
        }
      }
    }
  }

  private _reset(pdfId: string): void {
    this._values.set(pdfId, new Map());
    this._logicalValues.set(pdfId, new Map());
    this._references.set(pdfId, new Map());
    this._structure.set(pdfId, new Map());
    this._noise.set(pdfId, new Map());
    this._pageContexts.set(pdfId, new Map());
    this._documentContexts.set(pdfId, {});
    this._financialStructure.set(pdfId, null);
  }

  private _ingestValues(pdfId: string, model: DocumentValues): void {
    const byPage = new Map<number, DetectedValue[]>();
    const logicalValues = new Map<number, DetectedValue[]>();
    const references = new Map<number, DetectedReference[]>();
    const structure = new Map<number, DetectedStructure[]>();
    const noise = new Map<number, NoiseSpan[]>();
    const pageContexts = new Map<number, ValueContext>();
    for (const page of model.pages) {
      pageContexts.set(page.pageIndex, page.context);
      // Tolerated rather than required: a decoder seam may omit an empty array.
      const pageNoise = page.noise ?? [];
      if (pageNoise.length > 0) noise.set(page.pageIndex, pageNoise);
      for (const value of page.values) {
        const pageLogicalValues = logicalValues.get(page.pageIndex) ?? [];
        pageLogicalValues.push(value);
        logicalValues.set(page.pageIndex, pageLogicalValues);
        const segments = value.segments ?? [{ pageIndex: page.pageIndex, text: value.text, bounds: value.bounds }];
        for (const segment of segments) {
          const pageValues = byPage.get(segment.pageIndex) ?? [];
          pageValues.push({ ...value, bounds: segment.bounds });
          byPage.set(segment.pageIndex, pageValues);
        }
      }
      for (const reference of page.references ?? []) {
        const segments = reference.segments
          ?? [{ pageIndex: page.pageIndex, text: reference.text, bounds: reference.bounds }];
        for (const segment of segments) {
          const pageReferences = references.get(segment.pageIndex) ?? [];
          pageReferences.push({ ...reference, bounds: segment.bounds });
          references.set(segment.pageIndex, pageReferences);
        }
      }
      for (const span of page.structure ?? []) {
        const segments = span.segments
          ?? [{ pageIndex: page.pageIndex, text: span.text, bounds: span.bounds }];
        for (const segment of segments) {
          const pageStructure = structure.get(segment.pageIndex) ?? [];
          pageStructure.push({ ...span, bounds: segment.bounds });
          structure.set(segment.pageIndex, pageStructure);
        }
      }
    }
    this._values.set(pdfId, byPage);
    this._logicalValues.set(pdfId, logicalValues);
    this._references.set(pdfId, references);
    this._structure.set(pdfId, structure);
    this._noise.set(pdfId, noise);
    this._pageContexts.set(pdfId, pageContexts);
    this._documentContexts.set(pdfId, model.documentContext);
  }

  valuesOnPage(pdfId: string, pageIndex: number): DetectedValue[] {
    return this._values.get(pdfId)?.get(pageIndex) ?? [];
  }

  /** Spans that identify rather than measure. Click targets, exactly as values are. */
  referencesOnPage(pdfId: string, pageIndex: number): DetectedReference[] {
    return this._references.get(pdfId)?.get(pageIndex) ?? [];
  }

  /** Spans that are the document indexing itself: heading numbers, list ordinals. */
  structureOnPage(pdfId: string, pageIndex: number): DetectedStructure[] {
    return this._structure.get(pdfId)?.get(pageIndex) ?? [];
  }

  /** Spans the detector refused on this page. Diagnostics — never click targets. */
  noiseOnPage(pdfId: string, pageIndex: number): NoiseSpan[] {
    return this._noise.get(pdfId)?.get(pageIndex) ?? [];
  }

  /** What this page's captions say its figures are denominated in. */
  pageContext(pdfId: string, pageIndex: number): ValueContext {
    return this._pageContexts.get(pdfId)?.get(pageIndex) ?? {};
  }

  /** The same, read across the whole document. */
  documentContext(pdfId: string): ValueContext {
    return this._documentContexts.get(pdfId) ?? {};
  }

  has(pdfId: string): boolean { return this._values.has(pdfId); }

  valueCount(pdfId: string): number {
    let total = 0;
    for (const values of this._logicalValues.get(pdfId)?.values() ?? []) total += values.length;
    return total;
  }

  referenceCount(pdfId: string): number {
    let total = 0;
    for (const entries of this._references.get(pdfId)?.values() ?? []) total += entries.length;
    return total;
  }

  structureCount(pdfId: string): number {
    let total = 0;
    for (const entries of this._structure.get(pdfId)?.values() ?? []) total += entries.length;
    return total;
  }

  noiseCount(pdfId: string): number {
    let total = 0;
    for (const entries of this._noise.get(pdfId)?.values() ?? []) total += entries.length;
    return total;
  }

  logicalValuesOnPage(pdfId: string, pageIndex: number): DetectedValue[] {
    return this._logicalValues.get(pdfId)?.get(pageIndex) ?? [];
  }

  /** What the document appears to be. "neither" also when no structure was published. */
  documentClass(pdfId: string): FinancialDocumentClass {
    return this._financialStructure.get(pdfId)?.documentClass ?? "neither";
  }

  /**
   * What each apparatus was looked for and what it yielded. All zero and
   * unsearched when the document carries no structure artifact, which is what
   * distinguishes "not a financial document" from "a statement with no notes".
   */
  apparatus(pdfId: string): FinancialApparatus {
    return this._financialStructure.get(pdfId)?.apparatus ?? EMPTY_APPARATUS;
  }

  /** Canonical financial-statement notes detected across the document. */
  notes(pdfId: string): FinancialNote[] {
    return this._financialStructure.get(pdfId)?.notes ?? [];
  }

  /** Citations resolved to entries in the canonical note catalogue. */
  noteReferences(pdfId: string): FinancialNoteReference[] {
    return this._financialStructure.get(pdfId)?.noteReferences ?? [];
  }

  /** Canonical filing items detected across the document. */
  items(pdfId: string): FinancialItem[] {
    return this._financialStructure.get(pdfId)?.items ?? [];
  }

  /** Citations resolved to entries in the canonical item catalogue. */
  itemReferences(pdfId: string): FinancialItemReference[] {
    return this._financialStructure.get(pdfId)?.itemReferences ?? [];
  }

  clearPdf(pdfId: string): void {
    this._values.delete(pdfId);
    this._logicalValues.delete(pdfId);
    this._references.delete(pdfId);
    this._structure.delete(pdfId);
    this._noise.delete(pdfId);
    this._pageContexts.delete(pdfId);
    this._documentContexts.delete(pdfId);
    this._financialStructure.delete(pdfId);
    this._generations.set(pdfId, (this._generations.get(pdfId) ?? 0) + 1);
  }

  clear(): void {
    this._values.clear();
    this._logicalValues.clear();
    this._references.clear();
    this._structure.clear();
    this._noise.clear();
    this._pageContexts.clear();
    this._documentContexts.clear();
    this._financialStructure.clear();
    this._generations.clear();
    this._epoch++;
  }

  private _isCurrent(pdfId: string, epoch: number, generation: number): boolean {
    return this._epoch === epoch && (this._generations.get(pdfId) ?? 0) === generation;
  }
}
