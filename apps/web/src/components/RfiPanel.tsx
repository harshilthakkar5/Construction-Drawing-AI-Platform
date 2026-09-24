import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ManifestEntryDto, RfiDto, RfiPriority, RfiStatus } from "@cdip/shared";
import { RFI_PRIORITIES, RFI_STATUSES } from "@cdip/shared";
import { api } from "@/api";
import { FileSpreadsheetIcon, PlusIcon, SparklesIcon } from "lucide-react";
import { RfiReview } from "@/components/RfiReview";
import { Modal, Notice, TextArea, TextField } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store";

/**
 * The RFI tab: find them, review them, keep a log of them.
 *
 * The primary action is the scan (RfiReview) — the system finds the gaps and
 * words the questions, and a person accepts or dismisses each. Typing one by
 * hand is still possible and deliberately secondary.
 *
 * Whoever proposed the QUESTION, the ANSWER is always written by a person:
 * an RFI response is a contractual instruction someone builds from, so no
 * model writes into it and the answer box says so.
 *
 * Clicking a pinned location jumps the viewer to that page, which is the same
 * FR-18 path a chat citation takes.
 */

const STATUS_CHIP: Record<
  RfiStatus,
  { label: string; variant: "secondary" | "warning" | "success" | "destructive" }
> = {
  draft: { label: "Draft", variant: "secondary" },
  open: { label: "Open", variant: "warning" },
  answered: { label: "Answered", variant: "success" },
  closed: { label: "Closed", variant: "secondary" },
  voided: { label: "Voided", variant: "secondary" },
};

const PRIORITY_CHIP: Record<RfiPriority, "secondary" | "warning" | "destructive"> = {
  low: "secondary",
  normal: "secondary",
  high: "warning",
  critical: "destructive",
};

/** Mirrors the API's own rule, so the UI offers only buttons that will work.
 * The API refuses regardless — this exists to avoid showing a dead button,
 * never as the check itself. */
const NEXT: Record<RfiStatus, RfiStatus[]> = {
  draft: ["open", "voided"],
  open: ["voided"],
  answered: ["open", "closed", "voided"],
  closed: [],
  voided: [],
};

const TRANSITION_LABEL: Partial<Record<RfiStatus, string>> = {
  open: "Reopen",
  closed: "Close",
  voided: "Void",
};

function shortDate(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 10);
}

export function RfiPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<RfiStatus | "all">("all");
  const [composing, setComposing] = useState(false);
  const [openRfiId, setOpenRfiId] = useState<string | null>(null);

  const rfis = useQuery({
    queryKey: ["rfis", projectId, statusFilter],
    queryFn: () =>
      api.listRfis(projectId, statusFilter === "all" ? undefined : { status: statusFilter }),
  });

  const manifest = useQuery({
    queryKey: ["manifest", projectId],
    queryFn: () => api.manifest(projectId),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["rfis", projectId] });

  const counts = rfis.data?.reduce<Record<string, number>>((acc, rfi) => {
    acc[rfi.status] = (acc[rfi.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="px-4">
      <RfiReview projectId={projectId} />

      {/* One row: the log, its filter as a dropdown like every other filter
          in the app, and the two actions — which drop their labels when the
          work column is narrow rather than pushing the filter onto a row of
          its own. */}
      <div className="mb-3 flex items-center gap-2">
        <h4 className="shrink-0 text-sm font-semibold">RFI log</h4>
        <Select
          value={statusFilter}
          onValueChange={(value) => setStatusFilter(value as RfiStatus | "all")}
        >
          <SelectTrigger size="sm" className="max-w-40 min-w-0 text-xs" aria-label="Filter by status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {RFI_STATUSES.map((status) => (
              <SelectItem key={status} value={status}>
                {STATUS_CHIP[status].label}
                {counts?.[status] ? ` (${counts[status]})` : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          {/* A plain link, not fetch(): the browser saves the file itself, and
              the endpoint takes the session token as a query parameter for the
              same reason the media GETs do — a download cannot set a header. */}
          <Button size="sm" variant="outline" asChild>
            <a href={api.rfiExportUrl(projectId)} download title="Export the RFI log to Excel" aria-label="Export Excel">
              <FileSpreadsheetIcon />
              <span className="@[26rem]/work:inline hidden">Export Excel</span>
            </a>
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setComposing(true)}
            title="Add an RFI by hand"
            aria-label="Add manually"
          >
            <PlusIcon />
            <span className="@[26rem]/work:inline hidden">Add manually</span>
          </Button>
        </div>
      </div>

      {rfis.isLoading && <p className="text-muted-foreground text-sm">Loading RFIs…</p>}
      {rfis.error && <Notice tone="error">{(rfis.error as Error).message}</Notice>}

      {rfis.data?.length === 0 && (
        <p className="text-muted-foreground py-6 text-center text-sm">
          {statusFilter === "all"
            ? "No RFIs in the log yet. Accept a finding above, or add one manually."
            : `No ${STATUS_CHIP[statusFilter as RfiStatus].label.toLowerCase()} RFIs.`}
        </p>
      )}

      <ul className="flex flex-col gap-1">
        {rfis.data?.map((rfi) => (
          <li key={rfi.id}>
            <button
              type="button"
              onClick={() => setOpenRfiId(rfi.id)}
              className={cn(
                "hover:bg-accent w-full rounded-md px-2 py-2 text-left transition-colors",
              )}
            >
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground shrink-0 font-mono text-xs">
                  #{rfi.number}
                </span>
                <span className="truncate text-sm font-medium">{rfi.subject}</span>
                <Badge variant={STATUS_CHIP[rfi.status].variant} className="ml-auto shrink-0">
                  {STATUS_CHIP[rfi.status].label}
                </Badge>
              </div>
              <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-2 text-xs">
                {rfi.priority !== "normal" && (
                  <Badge variant={PRIORITY_CHIP[rfi.priority]}>{rfi.priority}</Badge>
                )}
                {rfi.source === "generated" && (
                  <span className="flex items-center gap-1" title="Found by a drawing scan">
                    <SparklesIcon className="size-3" />
                    found by scan
                  </span>
                )}
                {rfi.discipline && <span>{rfi.discipline}</span>}
                {rfi.locations.length > 0 && (
                  <span>
                    {rfi.locations
                      .map((l) => l.sheetNumber ?? `p.${l.combinedPageNumber ?? l.pageNumber}`)
                      .join(", ")}
                  </span>
                )}
                {rfi.locations.some((l) => l.drawingRevised) && (
                  <Badge variant="warning">sheet revised</Badge>
                )}
              </div>
            </button>
          </li>
        ))}
      </ul>

      {composing && (
        <ComposeRfi
          projectId={projectId}
          pages={manifest.data ?? []}
          onClose={() => setComposing(false)}
          onCreated={() => {
            setComposing(false);
            void refresh();
          }}
        />
      )}

      {openRfiId && (
        <RfiDetail
          projectId={projectId}
          rfiId={openRfiId}
          pages={manifest.data ?? []}
          onClose={() => setOpenRfiId(null)}
          onChanged={refresh}
        />
      )}
    </div>
  );
}

/**
 * Raise one.
 *
 * "Save as draft" and "Raise now" both allocate a number, because the number
 * comes from the project's counter at CREATE and is never reused. A draft is
 * private until issued, not unnumbered.
 */
function ComposeRfi({
  projectId,
  pages,
  onClose,
  onCreated,
}: {
  projectId: string;
  pages: ManifestEntryDto[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [subject, setSubject] = useState("");
  const [question, setQuestion] = useState("");
  const [priority, setPriority] = useState<RfiPriority>("normal");
  const [dueAt, setDueAt] = useState("");
  const [pageKey, setPageKey] = useState("");

  const selected = pages.find((p) => `${p.documentId}:${p.pageNumber}` === pageKey);

  const create = useMutation({
    mutationFn: (issue: boolean) =>
      api.createRfi(projectId, {
        subject,
        question,
        priority,
        dueAt: dueAt ? new Date(dueAt).toISOString() : null,
        discipline: selected?.discipline ?? null,
        issue,
        locations: selected
          ? [{ documentId: selected.documentId, pageNumber: selected.pageNumber }]
          : [],
      }),
    onSuccess: onCreated,
  });

  const ready = subject.trim().length > 0 && question.trim().length > 0;

  return (
    <Modal
      title="New RFI"
      description="A question the drawings do not answer. Pin it to the sheet it came from so whoever answers can see what you saw."
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button
            variant="outline"
            onClick={() => create.mutate(false)}
            disabled={!ready || create.isPending}
          >
            Save as draft
          </Button>
          <Button onClick={() => create.mutate(true)} disabled={!ready || create.isPending}>
            {create.isPending ? "Saving…" : "Raise now"}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <TextField
          label="Subject"
          value={subject}
          onChange={setSubject}
          placeholder="Pile cap PC4 not in schedule"
          required
        />
        <TextArea
          label="Question"
          value={question}
          onChange={setQuestion}
          rows={5}
          placeholder="PC4 appears at grid 7/D on S-101P but has no row in the Pile Cap Schedule. Please confirm the size and reinforcement."
          required
        />
        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-2">
            <label className="text-sm font-medium" htmlFor="rfi-priority">
              Priority
            </label>
            <select
              id="rfi-priority"
              className="border-input bg-background rounded-md border px-3 py-2 text-sm"
              value={priority}
              onChange={(e) => setPriority(e.target.value as RfiPriority)}
            >
              {RFI_PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <TextField label="Due date" type="date" value={dueAt} onChange={setDueAt} />
        </div>
        <div className="grid gap-2">
          <label className="text-sm font-medium" htmlFor="rfi-sheet">
            Sheet
          </label>
          <select
            id="rfi-sheet"
            className="border-input bg-background rounded-md border px-3 py-2 text-sm"
            value={pageKey}
            onChange={(e) => setPageKey(e.target.value)}
          >
            <option value="">No sheet — a general question</option>
            {pages.map((page) => (
              <option
                key={`${page.documentId}:${page.pageNumber}`}
                value={`${page.documentId}:${page.pageNumber}`}
              >
                {page.sheetNumber ?? `Page ${page.combinedPageNumber}`}
                {page.sheetNumber ? ` — page ${page.combinedPageNumber}` : ""}
              </option>
            ))}
          </select>
          <p className="text-muted-foreground text-xs">
            The sheet number and page are recorded as they are now, so the RFI still reads
            correctly if the drawing is later revised or removed.
          </p>
        </div>
        {!!create.error && <Notice tone="error">{(create.error as Error).message}</Notice>}
      </div>
    </Modal>
  );
}

/** One RFI: its question, its pins, its answer and its audit trail. */
function RfiDetail({
  projectId,
  rfiId,
  pages,
  onClose,
  onChanged,
}: {
  projectId: string;
  rfiId: string;
  pages: ManifestEntryDto[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const requestJump = useAppStore((s) => s.requestJump);
  const [answer, setAnswer] = useState("");

  const rfi = useQuery({
    queryKey: ["rfi", projectId, rfiId],
    queryFn: () => api.getRfi(projectId, rfiId),
  });

  const settle = (updated: RfiDto) => {
    queryClient.setQueryData(["rfi", projectId, rfiId], updated);
    onChanged();
  };

  const transition = useMutation({
    mutationFn: (status: RfiStatus) => api.setRfiStatus(projectId, rfiId, status),
    onSuccess: settle,
  });

  const submitAnswer = useMutation({
    mutationFn: () => api.answerRfi(projectId, rfiId, answer),
    onSuccess: (updated) => {
      setAnswer("");
      settle(updated);
    },
  });

  const addLocation = useMutation({
    mutationFn: (key: string) => {
      const page = pages.find((p) => `${p.documentId}:${p.pageNumber}` === key);
      if (!page) throw new Error("pick a sheet");
      return api.addRfiLocation(projectId, rfiId, {
        documentId: page.documentId,
        pageNumber: page.pageNumber,
      });
    },
    onSuccess: settle,
  });

  const removeLocation = useMutation({
    mutationFn: (locationId: string) => api.removeRfiLocation(projectId, rfiId, locationId),
    onSuccess: settle,
  });

  const data = rfi.data;
  const busy = transition.isPending || submitAnswer.isPending;
  const error = transition.error ?? submitAnswer.error ?? addLocation.error ?? removeLocation.error;

  return (
    <Modal
      title={data ? `RFI #${data.number} — ${data.subject}` : "RFI"}
      onClose={onClose}
      className="sm:max-w-2xl"
      footer={
        <>
          {data &&
            NEXT[data.status].map((status) => (
              <Button
                key={status}
                variant={status === "voided" ? "destructive" : "outline"}
                disabled={busy}
                onClick={() => transition.mutate(status)}
              >
                {TRANSITION_LABEL[status] ?? status}
              </Button>
            ))}
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </>
      }
    >
      {rfi.isLoading && <p className="text-muted-foreground text-sm">Loading…</p>}
      {data && (
        <div className="grid max-h-[60vh] gap-4 overflow-y-auto pr-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={STATUS_CHIP[data.status].variant}>
              {STATUS_CHIP[data.status].label}
            </Badge>
            <Badge variant={PRIORITY_CHIP[data.priority]}>{data.priority}</Badge>
            {data.discipline && <span className="text-muted-foreground text-xs">{data.discipline}</span>}
            {data.dueAt && (
              <span className="text-muted-foreground text-xs">due {shortDate(data.dueAt)}</span>
            )}
          </div>

          <section>
            <h4 className="mb-1 text-sm font-semibold">Question</h4>
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{data.question}</p>
            <p className="text-muted-foreground mt-1 text-xs">
              Raised {shortDate(data.createdAt)}
              {data.createdByName ? ` by ${data.createdByName}` : ""}
            </p>
          </section>

          <Separator />

          <section>
            <h4 className="mb-2 text-sm font-semibold">Where on the drawings</h4>
            {data.locations.length === 0 && (
              <p className="text-muted-foreground text-xs">
                Not pinned to a sheet — nothing for a reader to open.
              </p>
            )}
            <ul className="flex flex-col gap-1">
              {data.locations.map((loc) => (
                <li key={loc.id} className="flex items-center gap-2 text-sm">
                  <button
                    type="button"
                    className="text-primary hover:underline"
                    onClick={() =>
                      requestJump(loc.combinedPageNumber, loc.bbox ?? undefined)
                    }
                    disabled={loc.combinedPageNumber === null}
                  >
                    {loc.sheetNumber ?? loc.filename ?? "sheet"}
                    {loc.combinedPageNumber !== null && ` — page ${loc.combinedPageNumber}`}
                  </button>
                  {loc.drawingRevised && (
                    <Badge variant="warning" title="A newer revision of this sheet exists">
                      revised
                    </Badge>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto"
                    onClick={() => removeLocation.mutate(loc.id)}
                    disabled={removeLocation.isPending}
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
            <select
              className="border-input bg-background mt-2 w-full rounded-md border px-2 py-1 text-xs"
              value=""
              onChange={(e) => e.target.value && addLocation.mutate(e.target.value)}
              aria-label="Pin another sheet"
            >
              <option value="">Pin another sheet…</option>
              {pages.map((page) => (
                <option
                  key={`${page.documentId}:${page.pageNumber}`}
                  value={`${page.documentId}:${page.pageNumber}`}
                >
                  {page.sheetNumber ?? `Page ${page.combinedPageNumber}`}
                </option>
              ))}
            </select>
          </section>

          <Separator />

          <section>
            <h4 className="mb-2 text-sm font-semibold">Answer</h4>
            {data.answer ? (
              <>
                <p className="text-sm leading-relaxed whitespace-pre-wrap">{data.answer}</p>
                <p className="text-muted-foreground mt-1 text-xs">
                  {data.answeredByName ?? "Answered"} · {shortDate(data.answeredAt)}
                </p>
              </>
            ) : data.status === "open" ? (
              <div className="grid gap-2">
                <TextArea
                  label="Response"
                  value={answer}
                  onChange={setAnswer}
                  rows={4}
                  placeholder="The pile cap at 7/D is PC2. Refer to the schedule on S-500."
                />
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    onClick={() => submitAnswer.mutate()}
                    disabled={answer.trim().length === 0 || busy}
                  >
                    {submitAnswer.isPending ? "Saving…" : "Record answer"}
                  </Button>
                  {/* Said here rather than in a tooltip: it is the reason this
                      box has no "draft with AI" button beside it. */}
                  <p className="text-muted-foreground text-xs">
                    Written by you. An RFI answer is a contractual instruction, so nothing is
                    generated for this field.
                  </p>
                </div>
              </div>
            ) : data.status === "draft" ? (
              <p className="text-muted-foreground text-xs">
                Raise this RFI before answering it — a draft has not been issued to anyone yet.
              </p>
            ) : (
              <p className="text-muted-foreground text-xs">No answer was recorded.</p>
            )}
          </section>

          {data.events && data.events.length > 0 && (
            <>
              <Separator />
              <section>
                <h4 className="mb-2 text-sm font-semibold">History</h4>
                <ul className="text-muted-foreground flex flex-col gap-1 text-xs">
                  {data.events.map((event) => (
                    <li key={event.id}>
                      {shortDate(event.createdAt)} · {event.kind.replace(/_/g, " ")}
                      {event.actorName ? ` · ${event.actorName}` : ""}
                    </li>
                  ))}
                </ul>
              </section>
            </>
          )}

          {!!error && <Notice tone="error">{(error as Error).message}</Notice>}
        </div>
      )}
    </Modal>
  );
}
