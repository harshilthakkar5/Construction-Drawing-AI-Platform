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

  const selected = portions.data?.find((p) => p.id === selectedPortionId) ?? null;

  return (
    <div className="flex min-h-0 flex-col border-b border-gray-200">
      <h3 className="px-3 pt-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
        Portions
      </h3>
      <ul className="max-h-64 overflow-y-auto p-2">
        {portions.data?.map((portion) => (
          <li key={portion.id}>
            <button
              className={`mb-1 w-full rounded px-2 py-1.5 text-left text-sm hover:bg-blue-50 ${
                portion.id === selectedPortionId ? "bg-blue-100 font-medium" : ""
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
              <span>{portion.name}</span>
              <span className="float-right text-xs text-gray-500">
                pp. {portion.startPage}–{portion.endPage}
              </span>
            </button>
          </li>
        ))}
        {portions.data?.length === 0 && (
          <li className="px-2 py-1 text-sm text-gray-500">
            {processing ? "Detecting…" : "No portions yet — upload a PDF."}
          </li>
        )}
      </ul>

      {selected && (
        <div className="border-t border-gray-100 px-3 pb-2 text-xs text-gray-500">
          {selected.name}: combined pages {selected.startPage}–{selected.endPage}
        </div>
      )}
    </div>
  );
}
