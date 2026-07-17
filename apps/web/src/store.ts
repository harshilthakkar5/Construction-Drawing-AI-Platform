import { create } from "zustand";
import type { BBox, UserDto } from "@cdip/shared";

/** FR-19: a bounding-box highlight pinned to one combined page. bbox is in
 * PDF points; the viewer scales it with the page's pdfWidth/pdfHeight. */
export interface Highlight {
  combinedPageNumber: number;
  bbox: BBox;
}

/**
 * Global UI state. selectedProjectId doubles as navigation (no router yet);
 * selectedPortionId drives the summary panel / viewer jump / chat filter
 * (FR-16/FR-22). requestJump moves the viewer (FR-18) and optionally pins a
 * bbox highlight on the target page (FR-19).
 */
interface AppState {
  user: UserDto | null;
  setUser: (user: UserDto | null) => void;
  selectedProjectId: string | null;
  openProject: (projectId: string | null) => void;
  selectedPortionId: string | null;
  selectPortion: (portionId: string | null) => void;
  /** Combined page number the viewer should scroll to (FR-18 jump target). */
  jumpToPage: number | null;
  /** Active bbox highlight; persists until the next jump replaces/clears it. */
  highlight: Highlight | null;
  requestJump: (combinedPageNumber: number | null, bbox?: BBox | null) => void;
}

export const useAppStore = create<AppState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),
  selectedProjectId: null,
  openProject: (projectId) =>
    set({ selectedProjectId: projectId, jumpToPage: null, highlight: null, selectedPortionId: null }),
  selectedPortionId: null,
  selectPortion: (portionId) => set({ selectedPortionId: portionId }),
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
