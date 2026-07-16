import { create } from "zustand";

/**
 * Global UI state. selectedProjectId doubles as navigation (no router yet);
 * selectedPortionId will drive summary panel / viewer jump / chat filter
 * (FR-16/FR-22) in a later phase.
 */
interface AppState {
  selectedProjectId: string | null;
  openProject: (projectId: string | null) => void;
  selectedPortionId: string | null;
  selectPortion: (portionId: string | null) => void;
  /** Combined page number the viewer should scroll to (FR-18 jump target). */
  jumpToPage: number | null;
  requestJump: (combinedPageNumber: number | null) => void;
}

export const useAppStore = create<AppState>((set) => ({
  selectedProjectId: null,
  openProject: (projectId) => set({ selectedProjectId: projectId, jumpToPage: null }),
  selectedPortionId: null,
  selectPortion: (portionId) => set({ selectedPortionId: portionId }),
  jumpToPage: null,
  requestJump: (combinedPageNumber) => set({ jumpToPage: combinedPageNumber }),
}));
