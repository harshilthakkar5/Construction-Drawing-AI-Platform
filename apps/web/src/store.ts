import { create } from "zustand";
import type { BBox, UserDto } from "@cdip/shared";

/** FR-19: a bounding-box highlight pinned to one combined page. bbox is in
 * PDF points; the viewer scales it with the page's pdfWidth/pdfHeight. */
export interface Highlight {
  combinedPageNumber: number;
  bbox: BBox;
}

/**
 * Viewer filter: show only the pages carrying this discipline. Set by clicking
 * a category, cleared by "Show all pages" in the viewer. A discipline's pages
 * are routinely non-contiguous, so this is a per-page match, not a range.
 */
export interface PageFilter {
  discipline: string;
  label: string;
}

/** Top-level page. `project` is implied by selectedProjectId being set. */
export type View = "dashboard" | "projects" | "support" | "account";

/**
 * Global UI state. `view` + selectedProjectId are the navigation (no router);
 * selectedPortionId drives the summary panel / viewer jump / chat filter
 * (FR-16/FR-22). requestJump moves the viewer (FR-18) and optionally pins a
 * bbox highlight on the target page (FR-19).
 */
interface AppState {
  user: UserDto | null;
  setUser: (user: UserDto | null) => void;
  view: View;
  /** Switching pages also leaves any open project. */
  setView: (view: View) => void;
  selectedProjectId: string | null;
  openProject: (projectId: string | null) => void;
  selectedPortionId: string | null;
  selectPortion: (portionId: string | null) => void;
  /** Active category filter for the viewer's page list (null = every page). */
  pageFilter: PageFilter | null;
  setPageFilter: (filter: PageFilter | null) => void;
  /** Combined page number the viewer should scroll to (FR-18 jump target). */
  jumpToPage: number | null;
  /** Active bbox highlight; persists until the next jump replaces/clears it. */
  highlight: Highlight | null;
  requestJump: (combinedPageNumber: number | null, bbox?: BBox | null) => void;
}

export const useAppStore = create<AppState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),
  view: "dashboard",
  setView: (view) =>
    set({
      view,
      selectedProjectId: null,
      jumpToPage: null,
      highlight: null,
      selectedPortionId: null,
      pageFilter: null,
    }),
  selectedProjectId: null,
  openProject: (projectId) =>
    set({
      selectedProjectId: projectId,
      // Opening a project — and backing out of one — both belong to Projects.
      view: "projects",
      jumpToPage: null,
      highlight: null,
      selectedPortionId: null,
      pageFilter: null,
    }),
  selectedPortionId: null,
  selectPortion: (portionId) => set({ selectedPortionId: portionId }),
  pageFilter: null,
  setPageFilter: (pageFilter) => set({ pageFilter }),
  jumpToPage: null,
  highlight: null,
  // requestJump(page, bbox) → jump + highlight; requestJump(page) → plain
  // jump, clearing any highlight; requestJump(null) → the viewer signalling
  // "jump done" — the highlight stays visible.
  requestJump: (combinedPageNumber, bbox) =>
    set((state) => ({
      jumpToPage: combinedPageNumber,
      highlight:
        combinedPageNumber == null
          ? state.highlight
          : bbox
            ? { combinedPageNumber, bbox }
            : null,
    })),
}));
