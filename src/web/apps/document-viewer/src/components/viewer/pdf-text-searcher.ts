import { normalizeSearchQuery, searchPageWithIndex } from "@talliark/shared";
import type { TextContentCache } from "../../services/text-content-cache.js";
import type { ValuesCache } from "../../services/values-cache.js";
import type { PdfEntry, SearchMatch } from "../../types/index.js";

export { normalizeSearchQuery } from "@talliark/shared";

interface SearchPageRef {
  entry: PdfEntry;
  pageIndex: number;
}

export interface SearchBatch {
  matches: SearchMatch[];
  complete: boolean;
  hasMore: boolean;
}

export class PdfTextSearcher {
  private readonly _cache: TextContentCache;
  private readonly _valuesCache: ValuesCache | undefined;

  constructor(
    cache: TextContentCache,
    valuesCache?: ValuesCache,
  ) {
    this._cache = cache;
    this._valuesCache = valuesCache;
  }

  search(rawQuery: string, pdfEntries: PdfEntry[]): SearchMatch[] {
    const normalizedQuery = normalizeSearchQuery(rawQuery);
    if (!normalizedQuery) return [];

    const results: SearchMatch[] = [];

    for (const entry of pdfEntries) {
      if (!this._cache.has(entry.id)) continue;

      for (const pageIndex of this._cache.getPageIndices(entry.id)) {
        const entries = this._cache.get(entry.id, pageIndex);
        const searchIndex = this._cache.getSearchIndex(entry.id, pageIndex);
        if (!entries || entries.length === 0 || !searchIndex) continue;

        results.push(...searchPageIncludingMagnitudeAliases(
          this._cache, this._valuesCache, entry, pageIndex, normalizedQuery,
        ));
      }
    }

    return results.sort((a, b) => Number(b.exactMatch) - Number(a.exactMatch));
  }

  createSession(rawQuery: string, pdfEntries: PdfEntry[]): PdfTextSearchSession {
    return new PdfTextSearchSession(this._cache, this._valuesCache, normalizeSearchQuery(rawQuery), pdfEntries);
  }

  searchPage(rawQuery: string, entry: PdfEntry, pageIndex: number): SearchMatch[] {
    const normalizedQuery = normalizeSearchQuery(rawQuery);
    if (!normalizedQuery || !this._cache.has(entry.id)) return [];

    const entries = this._cache.get(entry.id, pageIndex);
    const searchIndex = this._cache.getSearchIndex(entry.id, pageIndex);
    if (!entries || entries.length === 0 || !searchIndex) return [];

    return searchPageIncludingMagnitudeAliases(this._cache, this._valuesCache, entry, pageIndex, normalizedQuery);
  }
}

export class PdfTextSearchSession {
  private readonly _cache: TextContentCache;
  private readonly _normalizedQuery: string;
  private readonly _pages: SearchPageRef[];
  private _pageCursor = 0;
  private _matchCursor = 0;
  private _currentPageMatches: SearchMatch[] | null = null;
  private _complete = false;
  private _priority: "exact" | "partial" = "exact";

  constructor(
    cache: TextContentCache,
    valuesCache: ValuesCache | undefined,
    normalizedQuery: string,
    pdfEntries: PdfEntry[],
  ) {
    this._cache = cache;
    this._valuesCache = valuesCache;
    this._normalizedQuery = normalizedQuery;
    this._pages = [];

    if (!this._normalizedQuery) {
      this._complete = true;
      return;
    }

    for (const entry of pdfEntries) {
      if (!this._cache.has(entry.id)) continue;

      for (const pageIndex of this._cache.getPageIndices(entry.id)) {
        this._pages.push({ entry, pageIndex });
      }
    }

    if (this._pages.length === 0) this._complete = true;
  }

  private readonly _valuesCache: ValuesCache | undefined;

  get hasMore(): boolean {
    return !this._complete;
  }

  nextBatch(
    limit: number,
    pageBudget = 12,
    timeBudgetMs = Number.POSITIVE_INFINITY,
  ): SearchBatch {
    if (this._complete || limit <= 0) {
      return { matches: [], complete: this._complete, hasMore: !this._complete };
    }

    const matches: SearchMatch[] = [];
    let pagesScanned = 0;
    const startedAt = performance.now();

    while (
      matches.length < limit
      && pagesScanned < pageBudget
      && !this._complete
      && (pagesScanned === 0 || performance.now() - startedAt < timeBudgetMs)
    ) {
      if (this._pageCursor >= this._pages.length) {
        if (this._priority === "exact") {
          this._priority = "partial";
          this._pageCursor = 0;
          this._matchCursor = 0;
          this._currentPageMatches = null;
          continue;
        }

        this._complete = true;
        break;
      }

      const page = this._pages[this._pageCursor]!;
      const entries = this._cache.get(page.entry.id, page.pageIndex);
      const searchIndex = this._cache.getSearchIndex(page.entry.id, page.pageIndex);

      if (!entries || entries.length === 0 || !searchIndex) {
        this._pageCursor++;
        this._matchCursor = 0;
        this._currentPageMatches = null;
        pagesScanned++;
        continue;
      }

      if (this._currentPageMatches === null) {
        this._currentPageMatches = searchPageIncludingMagnitudeAliases(
          this._cache,
          this._valuesCache,
          page.entry,
          page.pageIndex,
          this._normalizedQuery,
        ).filter((match) => match.exactMatch === (this._priority === "exact"));
      }
      const pageMatches = this._currentPageMatches;

      const initialResultCount = matches.length;
      for (let i = this._matchCursor; i < pageMatches.length && matches.length < limit; i++) {
        matches.push(pageMatches[i]!);
      }

      const appendedFromPage = matches.length - initialResultCount;
      if (this._matchCursor + appendedFromPage < pageMatches.length) {
        this._matchCursor += appendedFromPage;
        break;
      }

      this._pageCursor++;
      this._matchCursor = 0;
      this._currentPageMatches = null;
      pagesScanned++;
    }

    return { matches, complete: this._complete, hasMore: !this._complete };
  }
}

function searchPageIncludingMagnitudeAliases(
  cache: TextContentCache,
  valuesCache: ValuesCache | undefined,
  entry: PdfEntry,
  pageIndex: number,
  normalizedQuery: string,
): SearchMatch[] {
  const entries = cache.get(entry.id, pageIndex);
  const searchIndex = cache.getSearchIndex(entry.id, pageIndex);
  if (!entries || entries.length === 0 || !searchIndex) return [];

  const textMatches = searchPageWithIndex(
    entry.id, entry.name, pageIndex, entries, searchIndex, normalizedQuery,
  );
  if (!valuesCache) return textMatches;

  const aliases: SearchMatch[] = [];
  for (const value of valuesCache.logicalValuesOnPage(entry.id, pageIndex)) {
    if (!value.magnitude || !value.normalizedValue) continue;

    const displayForms = [value.text, value.text.replace(
      /^\s*(?:USD|EUR|GBP|JPY|CAD|AUD|CHF|CNY|INR|KRW|[$€£¥₹₩])\s*/i,
      "",
    )].map(normalizeSearchQuery);
    const matchesDisplayText = displayForms.includes(normalizedQuery);
    if (normalizedQuery !== value.normalizedValue && !matchesDisplayText) continue;

    if (matchesDisplayText && textMatches.some(
      (match) => displayForms.includes(normalizeSearchQuery(match.contextText)),
    )) continue;

    const duplicate = textMatches.some((match) => (
      Math.abs(match.highlightRect.x - value.bounds.x) < 0.000001
      && Math.abs(match.highlightRect.y - value.bounds.y) < 0.000001
      && Math.abs(match.highlightRect.width - value.bounds.width) < 0.000001
      && Math.abs(match.highlightRect.height - value.bounds.height) < 0.000001
    ));
    if (duplicate) continue;

    aliases.push({
      id: `${entry.id}:${pageIndex}:magnitude:${value.id}`,
      pdfId: entry.id,
      pdfName: entry.name,
      pageIndex,
      exactMatch: true,
      contextText: value.text,
      matchInContext: { start: 0, end: value.text.length },
      highlightRect: value.bounds,
    });
  }
  return aliases.concat(textMatches);
}
