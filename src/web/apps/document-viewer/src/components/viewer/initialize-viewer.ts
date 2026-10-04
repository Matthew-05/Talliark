import { createToolbar } from "../toolbar/toolbar.js";
import { ZoomController } from "../toolbar/zoom-controller.js";
import { LinkTypeSelector } from "../toolbar/link-type-selector.js";
import { connectViewerToHostBridge } from "./viewer-bridge.js";
import { RectDrawOverlay } from "./rect-draw-overlay.js";
import { RectEditOverlay } from "./rect-edit-overlay.js";
import { RectRenderer } from "./rect-renderer.js";
import { TableGridEditor } from "./table-grid-editor.js";
import { RectContextMenu } from "./rect-context-menu.js";
import { LinkSelectionPanel } from "./link-selection-panel.js";
import { CharBboxOverlay } from "./char-bbox-overlay.js";
import { TableSuggestionOverlay } from "./table-suggestion-overlay.js";
import { TableNotice } from "./table-notice.js";
import { createRectNavigator } from "./rect-navigator.js";
import { attachExcelKeyBridge } from "./excel-key-bridge.js";
import { createFitMode } from "./fit-mode.js";
import { measureVisiblePages } from "./page-visibility.js";
import { PdfTextSearcher } from "./pdf-text-searcher.js";
import { SearchMatchRenderer } from "./search-match-renderer.js";
import { createSearchNavigator } from "./search-navigator.js";
import { TableCopyModal } from "../table-copy-modal/table-copy-modal.js";
import { TextContentCache } from "../../services/text-content-cache.js";
import { TableStructureCache } from "../../services/table-structure-cache.js";
import { TableNoticeDismissals } from "../../services/table-notice-dismissals.js";
import { TableSuggestionVisibility } from "../../services/table-suggestion-visibility.js";
import { ValuesCache } from "../../services/values-cache.js";
import { ValuesOverlay } from "./values-overlay.js";
import { getSpanLinkTarget } from "./span-link-bounds.js";
import { extractText } from "@talliark/shared";
import {
  detectCopiedTable,
  detectTableGrid,
} from "../../services/table-extractor.js";
import {
  sendCopyTableSelection,
  sendLinkRectangleCreated,
  sendLinkRectangleUpdated,
  sendLinkRectangleClicked,
  sendLinkRectangleDeleted,
  sendCacheBuildStarted,
  sendCacheBuildComplete,
  sendOpenFileManager,
  sendRotatePage,
} from "../../host-bridge.js";
import type { DetectedTable } from "@talliark/shared";
import type {
  SearchMatch, LinkedRectEntry, LinkSelectionEntry, NormalizedRect, ZoomLevel,
} from "../../types/index.js";
import type { PdfEntry } from "../../types/index.js";
import type { PdfViewer } from "./pdf-viewer.js";

const SEARCH_RESULT_BATCH_SIZE = 50;
const SEARCH_PAGE_BATCH_SIZE = 24;
const SEARCH_TIME_BUDGET_MS = 8;
const SEARCH_RESULTS_PANEL_LIMIT = 500;
const SEARCH_BATCH_DELAY_MS = 20;

interface TalliarkDebugApi {
  toggleCharBboxes: () => boolean;
  showCharBboxes: () => void;
  hideCharBboxes: () => void;
  toggleValues: () => boolean;
  showValues: () => void;
  hideValues: () => void;
  toggleReferences: () => boolean;
  showReferences: () => void;
  hideReferences: () => void;
  toggleStructure: () => boolean;
  showStructure: () => void;
  hideStructure: () => void;
  toggleValueNoise: () => boolean;
  showValueNoise: () => void;
  hideValueNoise: () => void;
  toggleTableSuggestions: () => boolean;
  showTableSuggestions: () => void;
  hideTableSuggestions: () => void;
}

/**
 * Creates and wires the toolbar, rect-draw overlay, floating link-type bar,
 * and text-content cache to the viewer, then connects the host bridge.
 * Returns the toolbar element and a viewer wrapper (viewer + floating bar)
 * for the caller to mount in the DOM.
 */
export function initializeViewer(viewer: PdfViewer): { toolbarElement: HTMLElement; viewerWrapper: HTMLElement } {
  const {
    element: toolbarElement, zoom, page, folderFilter, selector, search, rotate, tableToggle,
  } =
    createToolbar();

  const linkTypeSelector = new LinkTypeSelector();
  viewer.onManageFilesRequested(sendOpenFileManager);

  viewer.onLoaded((total) => {
    page.setTotal(total);
    page.setCurrentPage(1);
  });

  let currentPage = 1;
  let onVisiblePageChanged = (): void => {};

  const fitMode = createFitMode(
    viewer,
    (scale) => {
      zoom.setScale(scale);
      viewer.setZoom(scale);
    },
    () => currentPage,
  );
  zoom.setFitActive(true);

  /** Applies a page-fit scale and keeps it fitted across later viewer resizes. */
  const applyFitZoom = (scale: ZoomLevel, pageNumber?: number): void => {
    zoom.setScale(scale);
    viewer.setZoom(scale);
    fitMode.enter(pageNumber);
    zoom.setFitActive(true);
  };

  // Only the +/- buttons and ctrl+wheel reach this callback, so it means the user
  // has chosen an explicit zoom level and no longer wants the page kept fitted.
  zoom.onChange((scale, anchor) => {
    fitMode.exit();
    zoom.setFitActive(false);
    viewer.setZoom(scale, anchor);
  });

  viewer.onLoaded(() => {
    currentPage = 1;
    zoom.setFitActive(fitMode.isActive());
  });

  page.onChange((pageNum) => {
    currentPage = pageNum;
    viewer.scrollToPage(pageNum);
    onVisiblePageChanged();
  });

  zoom.onFitToggle(() => {
    if (fitMode.isActive()) {
      fitMode.exit();
      zoom.setFitActive(false);
      return;
    }

    const fitScale = viewer.getPageFitScale(currentPage);
    if (fitScale === null) return;
    applyFitZoom(fitScale);
  });

  selector.onSelect((entry) => {
    void viewer.loadDocument(entry.url, entry.id, entry.pageRotations).then(() => viewer.startBackgroundRender());
  });

  rotate.onRotateCcw(() => {
    const pdfId = viewer.getActivePdfId();
    if (!pdfId) return;
    sendRotatePage(pdfId, currentPage - 1, "ccw");
  });

  rotate.onRotateCw(() => {
    const pdfId = viewer.getActivePdfId();
    if (!pdfId) return;
    sendRotatePage(pdfId, currentPage - 1, "cw");
  });

  viewer.element.addEventListener(
    "wheel",
    (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const rect = viewer.element.getBoundingClientRect();
      const anchor = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      zoom.adjustBy(
        e.deltaY > 0 ? -ZoomController.SCROLL_STEP : ZoomController.SCROLL_STEP,
        anchor
      );
    },
    { passive: false }
  );

  const onNavigateToPage = (pageNumber: number): void => {
    currentPage = pageNumber;
    fitMode.releasePin();
    page.setCurrentPage(pageNumber);
    onVisiblePageChanged();
  };

  const getVisiblePageMeasurements = (): Array<{ pageNumber: number; visibleHeight: number }> => {
    const viewerRect = viewer.element.getBoundingClientRect();
    const pageBounds = viewer.getPageLayout().map(({ pageNumber, wrapper }) => {
      const wrapperRect = wrapper.getBoundingClientRect();
      return { pageNumber, top: wrapperRect.top, bottom: wrapperRect.bottom };
    });
    return measureVisiblePages(viewerRect, pageBounds);
  };

  const getVisiblePageIndices = (): number[] => {
    const visiblePages = getVisiblePageMeasurements();
    return visiblePages.length > 0
      ? visiblePages.map(({ pageNumber }) => pageNumber - 1)
      : [currentPage - 1];
  };

  let visiblePageSetKey = "";

  const updatePageFromScroll = (): void => {
    const visiblePages = getVisiblePageMeasurements();
    if (visiblePages.length === 0) return;

    let mostVisiblePage = visiblePages[0]!.pageNumber;
    let maxVisibleHeight = visiblePages[0]!.visibleHeight;
    for (const { pageNumber, visibleHeight } of visiblePages.slice(1)) {
      if (visibleHeight <= maxVisibleHeight) continue;
      maxVisibleHeight = visibleHeight;
      mostVisiblePage = pageNumber;
    }

    const nextVisiblePageSetKey = visiblePages.map(({ pageNumber }) => pageNumber).join(",");
    const visiblePageSetChanged = nextVisiblePageSetKey !== visiblePageSetKey;
    visiblePageSetKey = nextVisiblePageSetKey;

    if (mostVisiblePage !== currentPage) {
      onNavigateToPage(mostVisiblePage);
    } else if (visiblePageSetChanged) {
      onVisiblePageChanged();
    }
  };

  viewer.element.addEventListener("scroll", updatePageFromScroll, { passive: true });

  viewer.element.addEventListener("mousedown", () => {
    search.blur();
    search.hideResults();
  }, { passive: true });

  // ── Text cache & rect-draw overlay ────────────────────────────────────────

  let _currentRects: LinkedRectEntry[] = [];
  let _currentSelection: LinkSelectionEntry[] = [];

  const computeLinkCounts = (rects: LinkedRectEntry[]): Record<string, number> => {
    const counts: Record<string, number> = {};
    for (const r of rects) counts[r.pdfId] = (counts[r.pdfId] ?? 0) + 1;
    return counts;
  };

  const cache           = new TextContentCache();
  const tableCache      = new TableStructureCache();
  const valuesCache   = new ValuesCache();
  const renderer        = new RectRenderer(viewer);
  const contextMenu     = new RectContextMenu();
  const selectionPanel  = new LinkSelectionPanel();
  const overlay         = new RectDrawOverlay(viewer, cache);
  /**
   * Whether the detected table model may shape link rectangles.
   *
   * The model and the overlay that visualizes it are one feature behind one
   * switch. Snapping a grid to the detector while its regions are invisible
   * would change what a table link contains with nothing on screen to explain
   * it, and no way for someone to compare the two. Off means the viewer uses
   * the same purely visual grid detection it always did.
   */
  const tableSuggestionVisibility = new TableSuggestionVisibility();
  let experimentalTableDetectionEnabled = false;
  const detectedTableAt = (
    pdfId: string, pageIndex: number, rect: NormalizedRect,
  ): DetectedTable | null => (
    experimentalTableDetectionEnabled && tableSuggestionVisibility.isEnabled(pdfId)
      ? tableCache.tableAt(pdfId, pageIndex, rect)
      : null
  );
  const editOverlay     = new RectEditOverlay(viewer, cache, detectedTableAt, renderer);
  const tableGridEditor = new TableGridEditor(viewer, cache, renderer);
  const tableCopyModal  = new TableCopyModal();
  const charBboxDebug   = new CharBboxOverlay(viewer, cache);
  const valuesOverlay = new ValuesOverlay(viewer, valuesCache);
  const tableSuggestions = new TableSuggestionOverlay(viewer, tableCache);
  const tableNotice     = new TableNotice();
  const matchRenderer   = new SearchMatchRenderer(viewer);
  const searcher        = new PdfTextSearcher(cache, valuesCache);
  const searchNavigator = createSearchNavigator(
    viewer, selector, matchRenderer, applyFitZoom, onNavigateToPage,
  );

  const enrichTableMetadata = (entry: LinkedRectEntry): LinkedRectEntry => {
    if (entry.linkType !== "table" || !entry.table) return entry;
    const detected = detectedTableAt(entry.pdfId, entry.page, entry.rect);
    if (!detected) return entry;
    const modelGrid = detectTableGrid(cache.get(entry.pdfId, entry.page), entry.rect, detected);
    return {
      ...entry,
      table: {
        ...entry.table,
        ...(modelGrid.headerRowCount ? { headerRowCount: modelGrid.headerRowCount } : {}),
        ...(modelGrid.textLineBoundaries
          ? { textLineBoundaries: modelGrid.textLineBoundaries }
          : {}),
      },
    };
  };

  /** Detector-derived hints are display state, so a rect keeps only fresh ones. */
  const stripTableMetadata = (entry: LinkedRectEntry): LinkedRectEntry => {
    if (entry.linkType !== "table" || !entry.table) return entry;
    const { headerRowCount: _header, textLineBoundaries: _lines, ...table } = entry.table;
    return { ...entry, table };
  };

  const noticeDismissals = new TableNoticeDismissals();

  /**
   * Tables linked in this viewer but not yet echoed back by the host.
   *
   * Creating a link is a round trip. Without this the suggestion the user just
   * accepted would sit there until the host answered. Cleared whenever the host
   * sends the authoritative list, so it can never outlive the truth.
   */
  let _pendingLinkedTables = new Set<string>();

  /** Tables still worth suggesting in the open document, as of the last sync. */
  let _remainingTables = 0;

  const linkedTableIds = (pdfId: string): Set<string> => {
    const ids = tableCache.tablesUnder(
      pdfId,
      _currentRects
        .filter((entry) => entry.pdfId === pdfId && entry.linkType === "table")
        .map((entry) => ({ page: entry.page, rect: entry.rect })),
    );
    for (const id of _pendingLinkedTables) ids.add(id);
    return ids;
  };

  /** Recomputes what is still worth suggesting, and who should be saying so. */
  const syncTableSuggestions = (): void => {
    tableToggle.element.hidden = !experimentalTableDetectionEnabled;
    if (!experimentalTableDetectionEnabled) {
      _remainingTables = 0;
      tableSuggestions.hide();
      tableNotice.setVisible(false);
      tableToggle.setActive(false);
      return;
    }
    const pdfId = viewer.getActivePdfId();
    if (!pdfId) {
      _remainingTables = 0;
      tableSuggestions.hide();
      tableToggle.setActive(false);
      tableSuggestions.setLinkedTableIds(new Set());
      tableToggle.setState({ detected: false, total: 0, remaining: 0 });
      tableNotice.setCount(0);
      tableNotice.setVisible(false);
      return;
    }
    const enabled = tableSuggestionVisibility.isEnabled(pdfId);
    if (enabled) tableSuggestions.show();
    else tableSuggestions.hide();
    tableToggle.setActive(enabled);
    const linked = linkedTableIds(pdfId);
    tableSuggestions.setLinkedTableIds(linked);
    const total = tableCache.tableCount(pdfId);
    const remaining = Math.max(0, total - linked.size);
    _remainingTables = remaining;
    tableToggle.setState({
      detected: tableCache.hasStructure(pdfId), total, remaining,
    });
    tableNotice.setCount(remaining);
    tableNotice.setVisible(
      !enabled && noticeDismissals.shouldShow(pdfId, remaining),
    );
  };

  const refreshTableMetadata = (): void => {
    _currentRects = _currentRects.map((entry) => experimentalTableDetectionEnabled
      ? enrichTableMetadata(stripTableMetadata(entry))
      : stripTableMetadata(entry));
    renderer.setRectangles(_currentRects);
    tableSuggestions.refresh();
    syncTableSuggestions();
  };

  const setTableModelEnabled = (enabled: boolean): void => {
    if (!experimentalTableDetectionEnabled) return;
    const pdfId = viewer.getActivePdfId();
    if (!pdfId || tableSuggestionVisibility.isEnabled(pdfId) === enabled) return;
    tableSuggestionVisibility.setEnabled(pdfId, enabled);
    if (enabled) tableSuggestions.show();
    else tableSuggestions.hide();
    tableToggle.setActive(enabled);
    refreshTableMetadata();
  };

  tableToggle.onToggle(setTableModelEnabled);

  // Showing the suggestions is itself an acknowledgement: the notice has said
  // what it had to say, and turning them off again should not bring it back.
  const putNoticeAway = (): void => {
    const pdfId = viewer.getActivePdfId();
    if (pdfId) noticeDismissals.dismiss(pdfId, _remainingTables);
  };

  tableNotice.onShow(() => {
    putNoticeAway();
    setTableModelEnabled(true);
  });
  tableNotice.onDismiss(() => {
    putNoticeAway();
    syncTableSuggestions();
  });

  // A document swap changes what there is to suggest, and whether anything
  // should be on screen saying so.
  viewer.onDocumentChanged(() => { syncTableSuggestions(); });

  /** Rectangle the viewer is currently showing; marked as active in the panel. */
  let _focusedRectId: string | null = null;

  const setLinkSelection = (entries: LinkSelectionEntry[]): void => {
    _currentSelection = entries;
    selectionPanel.setEntries(entries);
    selectionPanel.setActiveEntry(_focusedRectId);
    renderer.setSelectedRectangles(entries.map((e) => e.id));
  };

  const clearLinkSelection = (): void => setLinkSelection([]);

  const focusRectangle = (id: string): void => {
    _focusedRectId = id;
    selectionPanel.setActiveEntry(id);
  };

  let lastSearchResults: SearchMatch[] = [];
  let highlightSearchResults: SearchMatch[] = [];
  let focusedMatch: SearchMatch | null = null;
  let searchGeneration = 0;
  let activeSearchSession: ReturnType<PdfTextSearcher["createSession"]> | null = null;
  let searchHasMore = false;
  let searchBatchTimer: ReturnType<typeof setTimeout> | null = null;
  let publishedSearchResultCount = -1;
  let publishedSearchHasMore = false;
  let publishedSearchCanLoadMore = false;

  const clearSearchBatchTimer = (): void => {
    if (searchBatchTimer === null) return;
    clearTimeout(searchBatchTimer);
    searchBatchTimer = null;
  };

  const getActivePdfHighlightMatches = (activePdfId: string): SearchMatch[] => {
    const submittedQuery = search.getSubmittedQuery();
    const entry = selector.getEntry(activePdfId);

    const visiblePageIndices = getVisiblePageIndices();
    const visiblePageSet = new Set(visiblePageIndices);

    // Cell-click text is staged rather than submitted. Search only the visible
    // pages so selection changes stay cheap and never populate the cross-document
    // results panel. Scroll and document changes call this again for the new view.
    if (!submittedQuery) {
      const stagedQuery = search.getQuery();
      if (!stagedQuery || !entry) return [];

      return visiblePageIndices.flatMap((pageIndex) =>
        searcher.searchPage(stagedQuery, entry, pageIndex)
      );
    }

    const matches = new Map<string, SearchMatch>();

    for (const match of highlightSearchResults) {
      if (match.pdfId === activePdfId && visiblePageSet.has(match.pageIndex)) {
        matches.set(match.id, match);
      }
    }

    if (entry) {
      for (const pageIndex of visiblePageIndices) {
        for (const match of searcher.searchPage(submittedQuery, entry, pageIndex)) {
          matches.set(match.id, match);
        }
      }
    }

    return Array.from(matches.values());
  };

  const applyActivePdfHighlights = (): void => {
    const activePdfId = viewer.getActivePdfId();
    if (!activePdfId) {
      matchRenderer.clearMatches();
      return;
    }

    if (
      focusedMatch
      && focusedMatch.pdfId === activePdfId
      && focusedMatch.pageIndex === currentPage - 1
    ) {
      matchRenderer.setMatches([focusedMatch]);
      matchRenderer.highlightMatch(focusedMatch.id);
      return;
    }

    if (focusedMatch && focusedMatch.pdfId !== activePdfId) {
      focusedMatch = null;
    }

    matchRenderer.setMatches(getActivePdfHighlightMatches(activePdfId));
  };

  onVisiblePageChanged = () => {
    if (!search.getQuery()) return;
    applyActivePdfHighlights();
  };

  const publishSearchResults = (): void => {
    const canLoadMore = searchHasMore && lastSearchResults.length < SEARCH_RESULTS_PANEL_LIMIT;
    if (
      lastSearchResults.length !== publishedSearchResultCount
      || searchHasMore !== publishedSearchHasMore
      || canLoadMore !== publishedSearchCanLoadMore
    ) {
      search.setResults(lastSearchResults, searchHasMore, canLoadMore);
      publishedSearchResultCount = lastSearchResults.length;
      publishedSearchHasMore = searchHasMore;
      publishedSearchCanLoadMore = canLoadMore;
    }
  };

  const loadSearchBatch = (
    generation: number,
    limit: number,
    scheduleNext: boolean,
  ): void => {
    if (generation !== searchGeneration || !activeSearchSession) return;

    const batch = activeSearchSession.nextBatch(
      limit,
      SEARCH_PAGE_BATCH_SIZE,
      SEARCH_TIME_BUDGET_MS,
    );
    if (generation !== searchGeneration) return;

    if (batch.matches.length > 0) {
      highlightSearchResults = highlightSearchResults.concat(batch.matches);

      if (lastSearchResults.length < SEARCH_RESULTS_PANEL_LIMIT) {
        const remainingListSlots = SEARCH_RESULTS_PANEL_LIMIT - lastSearchResults.length;
        lastSearchResults = lastSearchResults.concat(batch.matches.slice(0, remainingListSlots));
      }
    }

    searchHasMore = batch.hasMore || highlightSearchResults.length > lastSearchResults.length;
    publishSearchResults();

    if (!scheduleNext || !batch.hasMore) return;

    searchBatchTimer = setTimeout(() => {
      searchBatchTimer = null;
      loadSearchBatch(generation, SEARCH_RESULT_BATCH_SIZE, true);
    }, SEARCH_BATCH_DELAY_MS);
  };

  const runSearch = (query: string): void => {
    const generation = ++searchGeneration;
    clearSearchBatchTimer();
    focusedMatch = null;
    activeSearchSession = null;
    searchHasMore = false;
    publishedSearchResultCount = -1;
    publishedSearchHasMore = false;
    publishedSearchCanLoadMore = false;

    if (!query) {
      search.clearResults();
      matchRenderer.clearMatches();
      lastSearchResults = [];
      highlightSearchResults = [];
      return;
    }

    lastSearchResults = [];
    highlightSearchResults = [];
    matchRenderer.clearMatches();

    const filteredEntries = selector.getFilteredEntries();
    const activePdfId = viewer.getActivePdfId();
    const activeEntry = activePdfId
      ? filteredEntries.find((entry) => entry.id === activePdfId)
      : undefined;

    // Complete the document the user is looking at in one pass. The remaining
    // documents use short, time-budgeted slices below, so these results stay
    // fully available and clickable throughout the workbook-wide scan.
    const activePdfResults = activeEntry ? searcher.search(query, [activeEntry]) : [];
    highlightSearchResults = activePdfResults;
    lastSearchResults = activePdfResults.slice(0, SEARCH_RESULTS_PANEL_LIMIT);

    const backgroundEntries = activeEntry
      ? filteredEntries.filter((entry) => entry.id !== activeEntry.id)
      : filteredEntries;
    activeSearchSession = searcher.createSession(
      query,
      backgroundEntries,
    );
    searchHasMore = activeSearchSession.hasMore
      || highlightSearchResults.length > lastSearchResults.length;
    publishSearchResults();
    // Visible-page highlights are independent of the workbook-wide scan. Draw
    // them once now, then refresh only when the visible page set changes.
    applyActivePdfHighlights();

    if (activeSearchSession.hasMore) {
      searchBatchTimer = setTimeout(() => {
        searchBatchTimer = null;
        loadSearchBatch(generation, SEARCH_RESULT_BATCH_SIZE, true);
      }, SEARCH_BATCH_DELAY_MS);
    }
  };

  // The filter narrows the document list and cross-document search alike. The
  // open document stays loaded even when it sits outside the selected folder.
  folderFilter.onChange((folderId) => {
    selector.setFolderFilter(folderId);
    const query = search.getSubmittedQuery();
    if (query) runSearch(query);
  });

  search.onQuery(runSearch);
  search.onShowMore(() => {
    clearSearchBatchTimer();
    loadSearchBatch(searchGeneration, SEARCH_RESULT_BATCH_SIZE, false);
  });

  search.onMatchClicked((match) => {
    focusedMatch = match;
    searchNavigator(match, lastSearchResults);
  });

  // Ctrl+F focuses the PDF text search; Ctrl+Shift+F opens the document selector
  // and focuses its filter box. Both are captured because the WebView otherwise
  // hands them to its own find UI.
  document.addEventListener(
    "keydown",
    (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.key.toLowerCase() !== "f") return;

      e.preventDefault();
      e.stopPropagation();

      if (e.shiftKey) {
        search.hideResults();
        selector.openWithSearchFocus();
        return;
      }

      selector.close();
      search.focus();
    },
    true
  );

  search.disable();

  // Tab/Shift+Tab, Enter/Shift+Enter and Ctrl+Z drive the Excel grid rather than
  // the WebView. Registered after the Ctrl+F handler above so search keeps its key.
  attachExcelKeyBridge();

  contextMenu.attachScrollTarget(viewer.element);

  overlay.onRectCreated((payload) => {
    const linkType = linkTypeSelector.getLinkType();
    const table = linkType === "table"
      ? detectTableGrid(
          cache.get(payload.pdfId, payload.page),
          payload.rect,
          detectedTableAt(payload.pdfId, payload.page, payload.rect),
        )
      : undefined;
    sendLinkRectangleCreated({ ...payload, linkType, ...(table ? { table } : {}) });
    renderer.addRectangle({
      id:    `temp-${Date.now()}`,
      pdfId: payload.pdfId,
      page:  payload.page,
      rect:  payload.rect,
      linkType,
      ...(table ? { table } : {}),
    });
  });

  tableSuggestions.onSuggestionClicked((pdfId, page, detectedTable) => {
    if (!experimentalTableDetectionEnabled) return;
    const rect = detectedTable.bounds;
    _pendingLinkedTables.add(detectedTable.id);
    syncTableSuggestions();
    const entries = cache.get(pdfId, page);
    const table = detectTableGrid(entries, rect, detectedTable);
    sendLinkRectangleCreated({
      pdfId,
      page,
      rect,
      text: extractText(entries, rect),
      linkType: "table",
      table,
    });
    renderer.addRectangle({
      id: `temp-${Date.now()}`,
      pdfId,
      page,
      rect,
      linkType: "table",
      table,
    });
  });

  valuesOverlay.onSpanClicked((pdfId, page, span) => {
    const selectedType = linkTypeSelector.getLinkType();
    const linkType = selectedType === "table" ? "auto" : selectedType;
    const { rect, text } = getSpanLinkTarget(span, page);
    sendLinkRectangleCreated({
      pdfId,
      page,
      rect,
      text,
      linkType,
    });
    renderer.addRectangle({
      id: `temp-financial-${Date.now()}`,
      pdfId,
      page,
      rect,
      linkType,
    });
  });

  editOverlay.onRectUpdated((payload) => {
    sendLinkRectangleUpdated(payload);
  });

  tableGridEditor.onTableUpdated((payload) => {
    sendLinkRectangleUpdated(payload);
  });

  tableGridEditor.onCopySelection(async (id) => {
    const entry = renderer.getRectangle(id);
    const totalPages = viewer.getDocument()?.numPages ?? 0;
    if (!entry?.table || totalPages < 1) return;

    const pages = await tableCopyModal.show(entry.page, totalPages);
    if (!pages) return;

    const targets = [];
    for (const page of pages) {
      // Give the viewer a paint opportunity between pages. Table extraction is managed
      // data work, but a large page range should not monopolize the WebView UI thread.
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      const pageEntries = cache.get(entry.pdfId, page);
      targets.push({
        page,
        table: detectCopiedTable(
          pageEntries,
          entry.rect,
          entry.table.columnBoundaries,
        ),
      });
    }
    sendCopyTableSelection(id, targets);
  });

  renderer.setClickGuard(() => editOverlay.consumeClickSuppression());

  renderer.onRectClicked((id) => {
    // Clicking a rectangle selects its own cell in Excel, ending any multi-cell
    // selection. The host re-publishes that cell's links, so a sum cell backed by
    // several rectangles repopulates the panel straight away.
    focusRectangle(id);
    clearLinkSelection();
    sendLinkRectangleClicked(id);
  });

  renderer.onRectContextMenu((id, x, y) => {
    contextMenu.show(x, y, id);
  });

  contextMenu.onDelete((id) => {
    sendLinkRectangleDeleted(id, true);
  });

  contextMenu.onDeleteKeepData((id) => {
    sendLinkRectangleDeleted(id, false);
  });

  let cacheGeneration = 0;

  viewer.onDocumentChanged(() => {
    const pdfId = viewer.getActivePdfId();
    const doc   = viewer.getDocument();
    if (!pdfId || !doc) return;

    const finish = (): void => {
      charBboxDebug.refresh();
      valuesOverlay.refresh();
      if (search.getQuery()) {
        applyActivePdfHighlights();
      }
    };

    if (cache.has(pdfId) && valuesCache.has(pdfId)) {
      finish();
      return;
    }

    const gen = ++cacheGeneration;
    sendCacheBuildStarted();

    const entry = selector.getEntry(pdfId);
    const buildPromise = entry
      ? cache.buildForUrl(pdfId, entry.url, entry.geometryBase64)
      : cache.buildFromDoc(pdfId, doc);

    void buildPromise
      .then(() => valuesCache.build(
        pdfId,
        entry?.documentValuesBase64,
        entry?.financialStructureBase64,
        // Never OCR'd: recognize values in the browser from the text cache.
        // When OCR values exist the recognizer is not consulted at all.
        entry?.documentValuesBase64 ? undefined : cache.geometryFor(pdfId),
      ))
      .then(() => {
        if (gen !== cacheGeneration) return;
        finish();
        sendCacheBuildComplete();
      })
      .catch(() => { if (gen === cacheGeneration) sendCacheBuildComplete(); });
  });

  // ── Console debug API ─────────────────────────────────────────────────────

  (window as Window & { __talliark?: TalliarkDebugApi }).__talliark = {
    toggleCharBboxes: () => charBboxDebug.toggle(),
    showCharBboxes:   () => charBboxDebug.show(),
    hideCharBboxes:   () => charBboxDebug.hide(),
    toggleValues: () => valuesOverlay.toggle(),
    showValues: () => valuesOverlay.show(),
    hideValues: () => valuesOverlay.hide(),
    toggleReferences: () => valuesOverlay.toggleReferences(),
    showReferences: () => valuesOverlay.showReferences(),
    hideReferences: () => valuesOverlay.hideReferences(),
    toggleStructure: () => valuesOverlay.toggleStructure(),
    showStructure: () => valuesOverlay.showStructure(),
    hideStructure: () => valuesOverlay.hideStructure(),
    toggleValueNoise: () => valuesOverlay.toggleNoise(),
    showValueNoise: () => valuesOverlay.showNoise(),
    hideValueNoise: () => valuesOverlay.hideNoise(),
    toggleTableSuggestions: () => {
      if (!experimentalTableDetectionEnabled) return false;
      const pdfId = viewer.getActivePdfId();
      if (!pdfId) return false;
      const enabled = !tableSuggestionVisibility.isEnabled(pdfId);
      setTableModelEnabled(enabled);
      return enabled;
    },
    showTableSuggestions: () => { if (experimentalTableDetectionEnabled) setTableModelEnabled(true); },
    hideTableSuggestions: () => setTableModelEnabled(false),
  };

  // ── Host bridge ───────────────────────────────────────────────────────────

  const navigate = createRectNavigator(
    viewer, selector, renderer, applyFitZoom, onNavigateToPage,
  );

  selectionPanel.onEntryClicked((entry) => {
    _focusedRectId = entry.id;
    navigate(entry.id, entry.pdfId, entry.page);
  });

  connectViewerToHostBridge(
    viewer,
    selector,
    folderFilter,
    cache,
    tableCache,
    valuesCache,
    (indexing) => {
      if (indexing) {
        search.disable();
      } else {
        search.enable();
        const query = search.getSubmittedQuery();
        if (query) {
          runSearch(query);
        } else if (search.getQuery()) {
          applyActivePdfHighlights();
        }
      }
    },
    () => {
      charBboxDebug.refresh();
      refreshTableMetadata();
      valuesOverlay.refresh();
    },
    {
      onSetTableDetectionEnabled: (enabled) => {
        experimentalTableDetectionEnabled = enabled;
        if (!enabled) {
          const pdfId = viewer.getActivePdfId();
          if (pdfId) tableSuggestionVisibility.setEnabled(pdfId, false);
        }
        refreshTableMetadata();
      },
      onViewerSurfaceShown: () => {
        // Let the native task pane/window and WebView2 settle before repainting.
        // The current page is rendered first; the shared viewer repairs the rest
        // without changing zoom, scroll position, document, or overlay state.
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            void viewer.refreshRendering(currentPage).catch((error: unknown) => {
              console.warn("[Talliark] Viewer repaint failed:", error);
            });
          });
        });
      },
      onLinkedRectangles: (rects) => {
        contextMenu.hide();
        // The authoritative list supersedes anything this viewer assumed.
        _pendingLinkedTables = new Set();
        _currentRects = rects.map(enrichTableMetadata);
        renderer.setRectangles(_currentRects);
        syncTableSuggestions();
        selector.updateLinkCounts(computeLinkCounts(_currentRects));
      },
      onLinkedRectangleAdded: (rect) => {
        if (_currentRects.some((current) => current.id === rect.id)) return;
        const enriched = enrichTableMetadata(rect);
        _currentRects = [..._currentRects, enriched];
        renderer.addRectangle(enriched);
        syncTableSuggestions();
        selector.updateLinkCounts(computeLinkCounts(_currentRects));
        focusRectangle(rect.id);
        navigate(rect.id, rect.pdfId, rect.page);
      },
      onNavigateToRectangle: (id, pdfId, page) => {
        focusRectangle(id);
        navigate(id, pdfId, page);
      },
      onSetCharBboxesVisible: (visible) => {
        if (visible) charBboxDebug.show();
        else charBboxDebug.hide();
      },
      onSetValuesVisible: (visible) => {
        if (visible) valuesOverlay.show();
        else valuesOverlay.hide();
      },
      onSetReferencesVisible: (visible) => {
        if (visible) valuesOverlay.showReferences();
        else valuesOverlay.hideReferences();
      },
      onSetStructureVisible: (visible) => {
        if (visible) valuesOverlay.showStructure();
        else valuesOverlay.hideStructure();
      },
      onSetValueNoiseVisible: (visible) => {
        if (visible) valuesOverlay.showNoise();
        else valuesOverlay.hideNoise();
      },
      onClearRectangleHighlight: () => { renderer.clearHighlight(); },
      onHighlightRectangle: (id) => { renderer.highlightRectangle(id); },
      onLinkSelectionChanged: setLinkSelection,
      onSetSearchQuery: (query) => {
        // setQuery synchronously cancels any submitted search before the staged
        // visible-page preview is rendered.
        search.setQuery(query);
        applyActivePdfHighlights();
      },
      onLinkRectanglesRemoved: (ids) => {
        contextMenu.hide();
        renderer.removeRectangles(ids);
        const removed = new Set(ids);
        _currentRects = _currentRects.filter((r) => !removed.has(r.id));
        // A table whose link is gone is a suggestion again.
        syncTableSuggestions();
        selector.updateLinkCounts(computeLinkCounts(_currentRects));
        setLinkSelection(_currentSelection.filter((e) => !removed.has(e.id)));
      },
      onPageRotationsUpdated: (pdfId, rotations) => {
        selector.updatePdfRotations(pdfId, rotations);
        if (pdfId === viewer.getActivePdfId()) {
          for (const [k, v] of Object.entries(rotations)) {
            viewer.setPageRotation(Number(k), v);
          }
        }
      },
    },
  );

  // ── Floating link-type bar & selection panel ──────────────────────────────

  const linkTypeBar = document.createElement("div");
  linkTypeBar.className = "link-type-bar";

  linkTypeBar.append(linkTypeSelector.element);

  const viewerWrapper = document.createElement("div");
  viewerWrapper.className = "viewer-wrapper";
  viewerWrapper.append(
    viewer.element, tableNotice.element, linkTypeBar, selectionPanel.element,
  );

  viewer.onDocumentAvailabilityChanged((hasDocument) => {
    toolbarElement.hidden = !hasDocument;
    linkTypeBar.hidden = !hasDocument;
    if (!hasDocument) syncTableSuggestions();
  });

  return { toolbarElement, viewerWrapper };
}
