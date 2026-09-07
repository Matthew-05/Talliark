import { initHostBridge, sendViewerContentReady } from "../../host-bridge.js";
import type { HostMessageHandlers } from "../../host-bridge.js";
import type { TextContentCache } from "../../services/text-content-cache.js";
import type { TableStructureCache } from "../../services/table-structure-cache.js";
import type { ValuesCache } from "../../services/values-cache.js";
import type { FolderEntry, PdfEntry } from "../../types/index.js";
import type { FolderFilter } from "../toolbar/folder-filter.js";
import type { PdfSelector } from "../toolbar/pdf-selector.js";
import type { PdfViewer } from "./pdf-viewer.js";

/** Pick the PDF to display after the host pushes an updated list. */
function pickEntryToLoad(entries: PdfEntry[], activeId: string | null): PdfEntry | undefined {
  if (entries.length === 0) return undefined;
  if (activeId) {
    return entries.find((entry) => entry.id === activeId) ?? entries[0];
  }
  return entries[0];
}

async function indexAllPdfs(
  cache: TextContentCache,
  tableCache: TableStructureCache,
  valuesCache: ValuesCache,
  entries: PdfEntry[],
): Promise<void> {
  cache.clear();
  tableCache.clear();
  valuesCache.clear();
  await Promise.allSettled(
    entries.map(async (entry) => {
      await Promise.allSettled([
        cache.buildForUrl(entry.id, entry.url, entry.geometryBase64),
        tableCache.build(entry.id, entry.tableStructureBase64),
        valuesCache.build(entry.id, entry.documentValuesBase64, entry.financialStructureBase64),
      ]);
    }),
  );
}

/**
 * Wires the WebView2 host bridge to the viewer and selector.
 *
 * On each `pdfs-loaded` message the selector entries are refreshed and the
 * active PDF is reloaded (falling back to the first entry when none is active)
 * so OCR updates and other storage changes are reflected in the viewer.
 */
/**
 * Handlers supplied by the caller. PDF lifecycle messages (`onPdfsLoaded`,
 * `onPdfUpdated`, `onPdfNameUpdated`, `onPdfRemoved`) are owned by this module
 * because they drive the selector, cache, and document loading.
 */
export type ViewerHostHandlers = Omit<
  HostMessageHandlers,
  | "onPdfsLoaded"
  | "onPdfUpdated"
  | "onPdfNameUpdated"
  | "onPdfRemoved"
  | "onShowPdf"
  | "onFoldersUpdated"
>;

export function connectViewerToHostBridge(
  viewer: PdfViewer,
  selector: PdfSelector,
  folderFilter: FolderFilter,
  cache: TextContentCache,
  tableCache: TableStructureCache,
  valuesCache: ValuesCache,
  onIndexingStateChange: (indexing: boolean) => void,
  onTableStructureChanged: () => void,
  handlers: ViewerHostHandlers = {},
): void {
  let indexingCount = 0;

  const startIndexing = (): void => {
    indexingCount++;
    if (indexingCount === 1) onIndexingStateChange(true);
  };

  const endIndexing = (): void => {
    indexingCount = Math.max(0, indexingCount - 1);
    if (indexingCount === 0) onIndexingStateChange(false);
  };

  const reloadEntry = async (entry: PdfEntry): Promise<void> => {
    selector.setActiveId(entry.id);
    await viewer.loadDocument(entry.url, entry.id, entry.pageRotations);
    await viewer.renderPageNow(1);
    viewer.startBackgroundRender();
  };

  initHostBridge({
    ...handlers,

    onFoldersUpdated: (folders, assignments) => {
      selector.updateFolderAssignments(assignments);
      folderFilter.setFolders(folders);
    },

    onPdfsLoaded: (entries, folders) => {
      void (async () => {
        try {
          selector.setEntries(entries);
          folderFilter.setFolders(folders);

          startIndexing();
          void indexAllPdfs(cache, tableCache, valuesCache, entries)
            // Geometry for one damaged PDF must not suppress table/value refreshes
            // that finished successfully for it or any other document.
            .finally(() => {
              onTableStructureChanged();
              endIndexing();
            });

          const target = pickEntryToLoad(entries, viewer.getActivePdfId());
          if (target) {
            await reloadEntry(target);
          } else {
            viewer.showNoPdfsState();
          }
        } finally {
          sendViewerContentReady();
        }
      })();
    },

    onPdfUpdated: (entry) => {
      const isFirstEntry = selector.getEntries().length === 0;
      selector.upsertEntry(entry);

      startIndexing();
      void (async () => {
        cache.clearPdf(entry.id);
        tableCache.clearPdf(entry.id);
        valuesCache.clearPdf(entry.id);
        await Promise.allSettled([
          cache.buildForUrl(entry.id, entry.url, entry.geometryBase64),
          tableCache.build(entry.id, entry.tableStructureBase64),
          valuesCache.build(entry.id, entry.documentValuesBase64, entry.financialStructureBase64),
        ]);
      })().finally(() => {
        onTableStructureChanged();
        endIndexing();
      });

      if (isFirstEntry || viewer.getActivePdfId() === entry.id) {
        void reloadEntry(entry);
      }
    },

    onPdfNameUpdated: (id, name) => {
      selector.updateEntryName(id, name);
    },

    // The user picked a document in the file manager. Page, zoom and highlight
    // state are left alone — this is a document swap, not a navigation.
    onShowPdf: (pdfId) => {
      if (viewer.getActivePdfId() === pdfId) return;
      const entry = selector.getEntry(pdfId);
      if (!entry) return;
      void reloadEntry(entry);
    },

    onPdfRemoved: (id) => {
      cache.clearPdf(id);
      tableCache.clearPdf(id);
      valuesCache.clearPdf(id);
      selector.removeEntry(id);
      if (viewer.getActivePdfId() === id) {
        const next = selector.getEntries()[0];
        if (next) void reloadEntry(next);
        else viewer.showNoPdfsState();
      }
    },
  });
}
