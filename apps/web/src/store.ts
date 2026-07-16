import { create } from "zustand";

/**
 * Global UI state (FR-16/FR-22): the selected portion drives the summary
 * panel, the viewer jump target, and the optional chat retrieval filter.
 */
interface AppState {
  selectedPortionId: string | null;
  selectPortion: (portionId: string | null) => void;
}

export const useAppStore = create<AppState>((set) => ({
  selectedPortionId: null,
  selectPortion: (portionId) => set({ selectedPortionId: portionId }),
}));
