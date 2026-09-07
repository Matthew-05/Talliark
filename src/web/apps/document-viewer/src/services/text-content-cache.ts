import {
  buildCharEntriesFromGeometry,
  buildSearchPageIndexFromEntries,
  decodeTextGeometry,
  extractTextGeometryFromPdfDocument,
  extractTextGeometryFromPdfUrl,
} from "@talliark/shared";
import * as pdfjsLib from "pdfjs-dist";
import type { SearchPageIndex } from "@talliark/shared";

export interface CharacterEntry {
  char: string;
  normLeft: number;
  normTop: number;
  normRight: number;
  normBottom: number;
  /** 0-based visual line index within the page. */
  lineIndex: number;
  /** Index of the source TextItem within the page's text content. */
  itemIndex: number;
  /** When true, word spaces are already encoded as space characters in the array. */
  spacesPrecomputed?: boolean;
}

export class TextContentCache {
  private readonly _cache = new Map<string, Map<number, CharacterEntry[]>>();
  private readonly _searchIndexCache = new Map<string, Map<number, SearchPageIndex>>();
  private readonly _geometryByPdfId = new Map<string, string>();
  private readonly _generations = new Map<string, number>();
  private _epoch = 0;

  /**
   * Builds the full character cache for every page by loading the PDF from
   * the given URL. Opens and destroys its own pdf.js document handle.
   * Skips work when the pdfId is already indexed.
   */
  async buildForUrl(
    pdfId: string,
    url: string,
    geometryBase64?: string,
  ): Promise<void> {
    if (geometryBase64 !== undefined) {
      if (geometryBase64) {
        this._geometryByPdfId.set(pdfId, geometryBase64);
      } else {
        this._geometryByPdfId.delete(pdfId);
      }
    }

    if (this.has(pdfId)) return;
    const epoch = this._epoch;
    const generation = this._generations.get(pdfId) ?? 0;

    const storedGeometry = this._geometryByPdfId.get(pdfId);
    if (storedGeometry) {
      const entries = buildCharEntriesFromGeometry(await decodeTextGeometry(storedGeometry));
      if (this._isCurrent(pdfId, epoch, generation)) this._cache.set(pdfId, entries);
      return;
    }

    const geometry = await extractTextGeometryFromPdfUrl(url);
    if (this._isCurrent(pdfId, epoch, generation)) {
      this._cache.set(pdfId, buildCharEntriesFromGeometry(geometry));
    }
  }

  /**
   * Builds the cache from an already-open document owned by the viewer.
   * Used as a fallback when the active PDF was not indexed yet.
   */
  async buildFromDoc(pdfId: string, doc: pdfjsLib.PDFDocumentProxy): Promise<void> {
    if (this.has(pdfId)) return;
    const epoch = this._epoch;
    const generation = this._generations.get(pdfId) ?? 0;

    const storedGeometry = this._geometryByPdfId.get(pdfId);
    if (storedGeometry) {
      const entries = buildCharEntriesFromGeometry(await decodeTextGeometry(storedGeometry));
      if (this._isCurrent(pdfId, epoch, generation)) this._cache.set(pdfId, entries);
      return;
    }

    const geometry = await extractTextGeometryFromPdfDocument(doc);
    if (this._isCurrent(pdfId, epoch, generation)) {
      this._cache.set(pdfId, buildCharEntriesFromGeometry(geometry));
    }
  }

  /** Returns true when every page of the PDF has been indexed. */
  has(pdfId: string): boolean {
    return this._cache.has(pdfId);
  }

  /** Returns all indexed pdfIds. */
  getIndexedPdfIds(): string[] {
    return Array.from(this._cache.keys());
  }

  /** Returns sorted 0-based page indices for an indexed PDF. */
  getPageIndices(pdfId: string): number[] {
    const pageMap = this._cache.get(pdfId);
    if (!pageMap) return [];
    return Array.from(pageMap.keys()).sort((a, b) => a - b);
  }

  /** Synchronous lookup. Returns null if the page cache was never built. */
  get(pdfId: string, pageIndex: number): CharacterEntry[] | null {
    return this._cache.get(pdfId)?.get(pageIndex) ?? null;
  }

  getSearchIndex(pdfId: string, pageIndex: number): SearchPageIndex | null {
    const entries = this.get(pdfId, pageIndex);
    if (!entries) return null;

    let pageMap = this._searchIndexCache.get(pdfId);
    if (!pageMap) {
      pageMap = new Map<number, SearchPageIndex>();
      this._searchIndexCache.set(pdfId, pageMap);
    }

    let searchIndex = pageMap.get(pageIndex);
    if (!searchIndex) {
      searchIndex = buildSearchPageIndexFromEntries(entries);
      pageMap.set(pageIndex, searchIndex);
    }

    return searchIndex;
  }

  /** Removes a single PDF from the cache (e.g. after OCR update). */
  clearPdf(pdfId: string): void {
    this._cache.delete(pdfId);
    this._searchIndexCache.delete(pdfId);
    this._generations.set(pdfId, (this._generations.get(pdfId) ?? 0) + 1);
  }

  clear(): void {
    this._cache.clear();
    this._searchIndexCache.clear();
    this._geometryByPdfId.clear();
    this._generations.clear();
    this._epoch++;
  }

  private _isCurrent(pdfId: string, epoch: number, generation: number): boolean {
    return this._epoch === epoch && (this._generations.get(pdfId) ?? 0) === generation;
  }
}
