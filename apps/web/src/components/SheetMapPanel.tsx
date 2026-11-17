import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRightIcon, LayersIcon, RefreshCwIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { buildSheetMap, levelLabel, userMessage, type SheetConnections, type SheetLink as Link } from "@cdip/shared";
import { api } from "@/api";
import { Notice, SheetLink, Spinner } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store";

/**
 * Levels and how the sheets connect (shared `buildSheetMap`): each level with
 * its sheets, and for the sheet picked here the four links — same level in
 * other disciplines, enlarged ↔ overall plan, the sheets it points at and the
 * sheets pointing at it. Read in the title-block step (workers/src/sheet_info.py).
 */
export function SheetMapPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const requestJump = useAppStore((s) => s.requestJump);
  const manifest = useQuery({ queryKey: ["manifest", projectId], queryFn: () => api.manifest(projectId) });
  const map = useMemo(() => buildSheetMap(manifest.data ?? []), [manifest.data]);
  const [open, setOpen] = useState<string | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const reread = useMutation({
    mutationFn: () => api.rescrapeRegion(projectId),
    onSuccess: () => {
      for (const key of ["region", "manifest", "portions", "processing"]) {
        void queryClient.invalidateQueries({ queryKey: [key, projectId] });
      }
    },
  });

  const pick = (link: Link) => {
    setPicked(link.combinedPageNumber);
    requestJump(link.combinedPageNumber);
  };
  const sheet = picked !== null ? map.sheets[picked] : undefined;
  const groups = [...map.levels.map((l) => ({ key: l.level, label: levelLabel(l.level), sheets: l.sheets }))];
  if (map.noLevel.length) groups.push({ key: "", label: "No level", sheets: map.noLevel });

  return (
    <section className="px-4" data-tour="levels">
      <div className="flex items-center gap-2 pb-2">
        <h3 className="flex min-w-0 flex-1 items-center gap-2 text-sm font-semibold">
          <LayersIcon className="text-muted-foreground size-4" />
          Levels
        </h3>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs"
          disabled={reread.isPending}
          onClick={() => reread.mutate()}
          title="Read every sheet's title, level and references again (a few seconds; under a cent of AI)"
        >
          {reread.isPending ? <Spinner /> : <RefreshCwIcon />}
          Re-read sheets
        </Button>
      </div>
      {reread.error && <Notice tone="error">{userMessage((reread.error as Error).message)}</Notice>}
      {manifest.isLoading ? (
        <Spinner />
      ) : !map.levels.length ? (
        <p className="text-muted-foreground text-xs">
          No levels read yet. Press Re-read sheets to find each sheet's level and the sheets it connects to.
        </p>
      ) : (
        <ul className="flex flex-col gap-0.5">
          {groups.map((group) => {
            const expanded = open === group.key;
            return (
              <li key={group.key || "none"}>
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : group.key)}
                  className="hover:bg-accent flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm"
                >
                  <ChevronRightIcon className={cn("text-muted-foreground size-4 transition-transform", expanded && "rotate-90")} />
                  <span className={cn("min-w-0 flex-1", !group.key && "text-muted-foreground")}>{group.label}</span>
                  <span className="text-muted-foreground text-xs tabular-nums">
                    {group.sheets.length} sheet{group.sheets.length === 1 ? "" : "s"}
                  </span>
                </button>
                {expanded && (
                  <div className="flex flex-wrap gap-1.5 px-8 pt-1 pb-2">
                    {group.sheets.map((link) => (
                      <SheetLink
                        key={link.combinedPageNumber}
                        sheet={link.sheetNumber}
                        page={link.combinedPageNumber}
                        title={link.title ?? undefined}
                        onOpen={() => pick(link)}
                      />
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {sheet && <Connections sheet={sheet} onOpen={pick} />}
    </section>
  );
}

function Connections({ sheet, onOpen }: { sheet: SheetConnections; onOpen: (link: Link) => void }) {
  const rows: [string, Link[]][] = [
    ["Same level, other disciplines", sheet.sameLevel],
    ["Overall plan", sheet.overall],
    ["Enlarged plans", sheet.enlarged],
    ["Points to", sheet.references],
    ["Pointed to by", sheet.referencedBy],
  ];
  const shown = rows.filter(([, links]) => links.length);
  return (
    <div className="bg-muted/40 mt-3 rounded-lg border p-3" aria-live="polite">
      <p className="text-sm font-semibold">
        {sheet.sheetNumber ?? `Page ${sheet.combinedPageNumber}`}
        {sheet.title && <span className="text-muted-foreground font-normal"> · {sheet.title}</span>}
      </p>
      <p className="text-muted-foreground mt-0.5 text-xs">
        {sheet.levels.length ? sheet.levels.map(levelLabel).join(", ") : "No level"}
        {sheet.kind && ` · ${sheet.kind.replace("_", " ")}`}
      </p>
      {shown.length === 0 && !sheet.missingReferences.length ? (
        <p className="text-muted-foreground mt-2 text-xs">No connected sheets found.</p>
      ) : (
        <dl className="mt-2 flex flex-col gap-2">
          {shown.map(([label, links]) => (
            <div key={label}>
              <dt className="text-muted-foreground text-xs">{label}</dt>
              <dd className="mt-1 flex flex-wrap gap-1.5">
                {links.map((link) => (
                  <SheetLink
                    key={link.combinedPageNumber}
                    sheet={link.sheetNumber}
                    page={link.combinedPageNumber}
                    title={link.title ?? undefined}
                    onOpen={() => onOpen(link)}
                  />
                ))}
              </dd>
            </div>
          ))}
          {sheet.missingReferences.length > 0 && (
            <div>
              <dt className="text-muted-foreground text-xs">Points to, not in this project</dt>
              <dd className="mt-1 text-xs">{sheet.missingReferences.join(", ")}</dd>
            </div>
          )}
        </dl>
      )}
    </div>
  );
}
