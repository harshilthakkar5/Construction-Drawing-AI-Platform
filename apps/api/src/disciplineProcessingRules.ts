import { UNSORTED } from "@cdip/shared";

/** The project's new choice: what was chosen before plus what was asked for.
 * Nothing is ever removed — a processed discipline stays processed. "all" adds
 * every discipline the project has pages of, and unsorted pages. */
export function chosenAfter(current: string[], asked: string[], present: string[], all: boolean): string[] {
  const next = new Set([...current, ...asked, ...(all ? [...present, UNSORTED] : [])]);
  return [...next].sort();
}
