import {
  libraryCatalogueIds,
  type RfiLibraryDto,
  type RfiLibraryQuestionDto,
  type RfiProjectSettings,
  type RfiReviewThinking,
  type RfiSetItem,
} from "@cdip/shared";

/**
 * A named RFI's plan request for ONE of its elements: the RFI's sheets (one
 * sheet alone, or the sheets compared), the element's chosen questions, and
 * the catalogue questions they link to — exactly what "By element" sends for
 * the same choice — on the project's review settings. Pure.
 */
export function setPlanRequest(
  sheets: string[],
  item: RfiSetItem,
  library: RfiLibraryDto,
  review: RfiProjectSettings["review"],
  budgetHonoured: boolean,
) {
  const element = library.packages.flatMap((p) => p.elements).find((e) => e.id === item.elementId);
  if (!element) throw new Error(`element ${item.elementId} is not in the library`);
  const flat = (qs: RfiLibraryQuestionDto[]): RfiLibraryQuestionDto[] => qs.flatMap((q) => [q, ...flat(q.children)]);
  const chosen = flat(element.questions).filter((q) => item.questionIds.includes(q.id));
  if (!chosen.length) throw new Error(`no question chosen for ${element.name}`);
  return {
    target: sheets.length === 1 ? { type: "sheet" as const, value: sheets[0]! } : { type: "compare" as const, values: sheets },
    checkMode: "custom" as const,
    checkIds: libraryCatalogueIds(element, chosen),
    depth: review.depth,
    thinking: review.thinking as RfiReviewThinking,
    ...(review.provider && review.model ? { provider: review.provider, model: review.model } : {}),
    ...(review.maxInputTokens != null ? { maxInputTokens: review.maxInputTokens } : {}),
    // A numeric thinking ceiling only where the model honours one, as the plan form does.
    ...(budgetHonoured && review.maxThinkingTokens != null ? { maxThinkingTokens: review.maxThinkingTokens } : {}),
    excludePageIds: [] as string[],
    library: { elementId: element.id, questionIds: chosen.map((q) => q.id) },
  };
}

/** Sum of the reviews' price ranges; null as soon as one is unpriced. Pure. */
export function setCost(estimates: ({ costLowUsd: number | null; costHighUsd: number | null } | null)[]): {
  costLowUsd: number | null;
  costHighUsd: number | null;
} {
  if (!estimates.length) return { costLowUsd: null, costHighUsd: null };
  let low = 0;
  let high = 0;
  for (const e of estimates) {
    if (e?.costLowUsd == null || e.costHighUsd == null) return { costLowUsd: null, costHighUsd: null };
    low += e.costLowUsd;
    high += e.costHighUsd;
  }
  return { costLowUsd: low, costHighUsd: high };
}
