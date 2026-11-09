import { UNSORTED } from "@cdip/shared";

/** The project's new choice: what was chosen before plus what was asked for.
 * Nothing is ever removed — a processed discipline stays processed. "all" adds
 * every discipline the project has pages of, and unsorted pages. */
export function chosenAfter(current: string[], asked: string[], present: string[], all: boolean): string[] {
  const next = new Set([...current, ...asked, ...(all ? [...present, UNSORTED] : [])]);
  return [...next].sort();
}

export interface ProcessingUsageRow {
  stage: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}

/** What processing each discipline has cost, from usage rows the worker tags
 * "process:<discipline>" (workers/src/processing.py `process_stage`). */
export function processingSpend(
  rows: ProcessingUsageRow[],
  price: (row: ProcessingUsageRow) => number,
): Map<string, { costUsd: number; tokens: number }> {
  const spend = new Map<string, { costUsd: number; tokens: number }>();
  for (const row of rows) {
    if (!row.stage.startsWith("process:")) continue;
    const key = row.stage.slice("process:".length);
    const total = spend.get(key) ?? { costUsd: 0, tokens: 0 };
    total.costUsd += price(row);
    total.tokens += row.inputTokens + row.outputTokens + row.cacheReadTokens + row.cacheWriteTokens;
    spend.set(key, total);
  }
  return spend;
}
