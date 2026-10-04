import type { ZoomLevel } from "../../types/index.js";
import type { PdfViewer } from "./pdf-viewer.js";

/** Relative change below which a re-fit is not worth the re-render. */
const SCALE_EPSILON = 0.005;

export interface FitMode {
  /**
   * Marks the current zoom as a page-fit, so viewer resizes re-fit.
   *
   * Navigation applies its fit scale before the page controller catches up, so
   * callers mid-navigation pass their target page to pin it; without the pin a
   * resize landing in that window would re-fit whatever page is still current.
   */
  enter(pageNumber?: number): void;
  /** Clears any pinned page — navigation has finished and the page is current. */
  releasePin(): void;
  /** Leaves fit mode — the user has chosen an explicit zoom level. */
  exit(): void;
  isActive(): boolean;
  dispose(): void;
}

/**
 * Keeps the visible page fitted while the viewer element changes size.
 *
 * A fit scale is only correct for the viewport it was measured against, and the
 * viewport is not stable when it matters most: the first time the host reveals
 * the viewer, WebView2 lays out at an interim size and settles on the real one
 * a few frames later — after navigation has already measured and zoomed. Rather
 * than trying to predict when the size has settled, fit mode reacts to it, which
 * also keeps the page fitted when the user drags the task-pane splitter.
 *
 * Fit mode is entered whenever a fit scale is applied (the Fit button, rectangle
 * navigation, search navigation) and left when the user toggles Fit off or picks
 * an explicit zoom level.
 */
export function createFitMode(
  viewer: PdfViewer,
  applyZoom: (scale: ZoomLevel) => void,
  getCurrentPage: () => number,
): FitMode {
  let active = false;
  let pinnedPage: number | null = null;

  const refit = (): void => {
    if (!active) return;

    const pageNumber = pinnedPage ?? getCurrentPage();
    const scale = viewer.getPageFitScale(pageNumber);
    if (scale === null) return;

    const current = viewer.getCurrentZoom();
    if (Math.abs(scale - current) <= current * SCALE_EPSILON) return;

    applyZoom(scale);

    // Re-fitting rescales every page wrapper, so the scroll offset no longer
    // frames the page it was fitted to. Snap back to it.
    const wrapper = viewer.element.querySelector<HTMLDivElement>(
      `[data-page="${pageNumber}"]`,
    );
    wrapper?.scrollIntoView({ behavior: "instant" as ScrollBehavior });
  };

  // Observing the border box (the default) rather than the content box keeps a
  // scrollbar appearing or disappearing from re-triggering this and oscillating.
  const observer = new ResizeObserver(refit);
  observer.observe(viewer.element);

  return {
    enter: (pageNumber) => {
      active = true;
      pinnedPage = pageNumber ?? null;
    },
    releasePin: () => { pinnedPage = null; },
    exit: () => {
      active = false;
      pinnedPage = null;
    },
    isActive: () => active,
    dispose: () => { observer.disconnect(); },
  };
}
