import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useAppStore } from "../store";

/**
 * FR-16: clicking a portion jumps the viewer to its start page and selects it
 * so the left panel can show that portion's summary. The summary itself lands
 * in Phase 4 — the selected panel below the list is its placeholder slot.
 */
export function PortionsPanel({ projectId }: { projectId: string }) {
  const selectedPortionId = useAppStore((s) => s.selectedPortionId);
  const selectPortion = useAppStore((s) => s.selectPortion);
  const requestJump = useAppStore((s) => s.requestJump);

  // Deduped against DocumentsPanel's query; used only to decide polling.
  const documents = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.listDocuments(projectId),
  });
  const processing = documents.data?.some(
    (d) => d.status === "uploaded" || d.status === "processing",
  );

  const portions = useQuery({
    queryKey: ["portions", projectId],
    queryFn: () => api.listPortions(projectId),
    refetchInterval: processing ? 3000 : false,
  });

  const selected =
    portions.data?.find((p) => p.id === selectedPortionId) ?? null;

  return (
    <section className="border-b border-hairline">
      <h3 className="sticky top-0 z-10 bg-surface px-3 pb-1 pt-3 text-xs font-semibold uppercase tracking-wide text-ink-muted">
        Portions
      </h3>
      <ul className="p-2">
        {portions.data?.map((portion) => (
          <li key={portion.id}>
            <button
              className={`mb-1 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition hover:bg-page ${
                portion.id === selectedPortionId
                  ? "bg-brand-50 font-semibold text-brand-700"
                  : "text-ink-soft"
              }`}
              onClick={() => {
                if (portion.id === selectedPortionId) {
                  selectPortion(null); // toggle back to the project summary
                  return;
                }
                selectPortion(portion.id);
                requestJump(portion.startPage);
              }}
            >
              <span className="min-w-0 flex-1 truncate">{portion.name}</span>
              <span className="shrink-0 text-xs tabular-nums text-ink-muted">
                pp. {portion.startPage}–{portion.endPage}
              </span>
            </button>
          </li>
        ))}
        {portions.data?.length === 0 && (
          <li className="px-2 py-1 text-sm text-ink-muted">
            {processing ? "Detecting…" : "No portions yet — upload a PDF."}
          </li>
        )}
      </ul>

      {selected && (
        <div className="border-t border-hairline px-3 pb-2 text-xs text-ink-muted">
          {selected.name}: combined pages {selected.startPage}–
          {selected.endPage}
        </div>
      )}
    </section>
  );
}
