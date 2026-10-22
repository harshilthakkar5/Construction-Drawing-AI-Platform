import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDownIcon, ChevronRightIcon, ClipboardListIcon, FileQuestionIcon, Maximize2Icon, PlayIcon, PlusIcon, RefreshCwIcon, SearchIcon, Trash2Icon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  RFI_SET_MAX_ELEMENTS,
  RFI_SET_NAME_CHARS,
  suggestSheets,
  type RfiLibraryDto,
  type RfiLibraryElementDto,
  type RfiReviewStatus,
  type RfiSetDetailDto,
  type RfiSetDto,
} from "@cdip/shared";
import { api } from "@/api";
import { AllBox, flat, QuestionTree, SheetPicker, useLibrarySheets } from "@/components/LibraryPicker";
import { CandidateCard } from "@/components/RfiReview";
import { Notice, Spinner } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store";

/** elementId → the question ids chosen under it. */
type Picks = Record<string, string[]>;

/** A review takes at most this many questions (the plan request's own cap). */
const QUESTIONS_PER_ELEMENT = 60;
const ACTIVE: RfiReviewStatus[] = ["queued", "running"];

const allQuestions = (e: RfiLibraryElementDto) => flat(e.questions).map((q) => q.id).slice(0, QUESTIONS_PER_ELEMENT);

/** Every word of the search, anywhere in the text, any case. */
const matcher = (query: string) => {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return (text: string) => words.length > 0 && words.every((w) => text.toLowerCase().includes(w));
};
/** Most search hits shown at once; past it the person narrows the words. */
const SEARCH_LIMIT = 60;

type Hit = { pkg: RfiLibraryDto["packages"][number]; element: RfiLibraryElementDto; questions: ReturnType<typeof flat> };

/** Elements whose name, and questions whose words, match the search. */
function searchLibrary(packages: RfiLibraryDto["packages"], query: string): { out: Hit[]; more: boolean } {
  const match = matcher(query);
  const out: Hit[] = [];
  let shown = 0;
  for (const pkg of packages) {
    for (const element of pkg.elements) {
      const questions = flat(element.questions).filter((q) => match(q.text));
      if (!match(`${element.name} ${element.variants}`) && !questions.length) continue;
      if (shown >= SEARCH_LIMIT) return { out, more: true };
      shown += Math.max(1, questions.length);
      out.push({ pkg, element, questions });
    }
  }
  return { out, more: false };
}

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="relative">
      <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
      <Input
        value={value}
        placeholder={placeholder}
        className="h-10 pl-9 text-base"
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && value && (e.stopPropagation(), onChange(""))}
      />
      {value && (
        <button type="button" aria-label="Clear search" onClick={() => onChange("")}
          className="text-muted-foreground hover:text-foreground absolute top-1/2 right-3 -translate-y-1/2">
          <XIcon className="size-4" />
        </button>
      )}
    </div>
  );
}

/** A stage's elements all on (every question) or all off. */
const setStage = (pkg: RfiLibraryDto["packages"][number], on: boolean): Picks =>
  Object.fromEntries(pkg.elements.map((e) => [e.id, on ? allQuestions(e) : []]));

function usd(low: number | null, high: number | null): string | null {
  if (low == null || high == null) return null;
  const f = (n: number) => (n < 0.01 ? "<$0.01" : `$${n.toFixed(2)}`);
  return low.toFixed(2) === high.toFixed(2) ? f(low) : `${f(low)}–${f(high)}`;
}

/** One word for where an RFI is, from its reviews by status. */
function setStatus(runs: RfiSetDto["runs"]): { label: string; tone: "default" | "secondary" | "outline" | "destructive" } {
  const n = (s: RfiReviewStatus) => runs[s] ?? 0;
  if (n("queued") + n("running")) return { label: "Running", tone: "default" };
  if (n("planned")) return { label: "Ready to start", tone: "secondary" };
  if (n("ready") + n("partial")) return { label: "Done", tone: "outline" };
  if (n("failed") + n("stale")) return { label: "Needs attention", tone: "destructive" };
  return { label: "Not priced", tone: "outline" };
}

const REVIEW_LABEL: Record<RfiReviewStatus, string> = {
  planned: "Priced, not started",
  queued: "Waiting",
  running: "Running",
  ready: "Done",
  partial: "Done (partly)",
  failed: "Failed",
  cancelled: "Cancelled",
  stale: "Drawings changed, price again",
} as Record<RfiReviewStatus, string>;

/**
 * The RFIs a person creates in a project ("Create RFI"): each has a name, the
 * sheets to read and the elements to check, and collects the findings of its
 * own reviews.
 */
export function RfiSetsSection({ projectId, onFinished }: { projectId: string; onFinished: () => void }) {
  const sets = useQuery({
    queryKey: ["rfi-sets", projectId],
    queryFn: () => api.listRfiSets(projectId),
    refetchInterval: (q) => ((q.state.data ?? []).some((s) => ACTIVE.some((a) => s.runs[a])) ? 4000 : false),
  });
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<{ id: string; errors: RfiSetDetailDto["planErrors"] } | null>(null);
  const openId = useAppStore((s) => s.openRfiSetId);
  const setOpenId = useAppStore((s) => s.setOpenRfiSetId);
  // A deleted set, or one that has not loaded yet, falls back to the list.
  const current = sets.data?.find((s) => s.id === openId) ?? null;

  return (
    <section className="flex flex-col gap-3">
      {/* Breadcrumb routing: "RFIs" is the list, "RFIs / <name>" one RFI. */}
      <div className="flex items-center justify-between gap-2">
        <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 text-base font-semibold">
          {current ? (
            <button
              type="button"
              onClick={() => setOpenId(null)}
              className="text-muted-foreground hover:text-foreground flex shrink-0 items-center gap-1.5 transition-colors"
              title="Back to all RFIs"
            >
              <ClipboardListIcon className="size-4" />
              RFIs
            </button>
          ) : (
            <span className="flex items-center gap-1.5">
              <ClipboardListIcon className="text-muted-foreground size-4" />
              RFIs {!!sets.data?.length && <span className="text-muted-foreground font-normal">({sets.data.length})</span>}
            </span>
          )}
          {current && (
            <>
              <ChevronRightIcon className="text-muted-foreground size-4 shrink-0" />
              <span className="flex min-w-0 items-center gap-1.5" aria-current="page">
                <FileQuestionIcon className="text-primary size-4 shrink-0" />
                <span className="truncate">{current.name}</span>
              </span>
            </>
          )}
        </nav>
        {current ? (
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={`Open ${current.name} in full view`}
            title="Full view: elements, price, start and findings"
            onClick={() => setOpen({ id: current.id, errors: [] })}
          >
            <Maximize2Icon />
          </Button>
        ) : (
          <Button size="sm" onClick={() => setCreating(true)}>
            <PlusIcon /> Create RFI
          </Button>
        )}
      </div>
      {current ? (
        <div className="flex flex-col gap-3">
          <SetSummary set={current} />
          <SetFindings projectId={projectId} setId={current.id} onFinished={onFinished} />
        </div>
      ) : sets.isLoading ? (
        <Spinner />
      ) : sets.error ? (
        <Notice tone="error">{(sets.error as Error).message}</Notice>
      ) : !sets.data?.length ? (
        <p className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm leading-relaxed">
          No RFIs yet. Create one: give it a name, pick the sheets, then the elements and questions to check. Each
          RFI keeps its own findings.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {sets.data.map((s) => (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => setOpenId(s.id)}
                className="bg-card hover:bg-accent/50 flex w-full items-start gap-2 rounded-lg border p-3 text-left transition-colors"
              >
                <FileQuestionIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
                <SetSummary set={s} title />
                <ChevronRightIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {creating && (
        <CreateRfiDialog
          projectId={projectId}
          onClose={() => setCreating(false)}
          onCreated={(detail) => {
            setCreating(false);
            setOpen({ id: detail.id, errors: detail.planErrors });
          }}
        />
      )}
      {open && (
        <RfiSetDialog
          projectId={projectId}
          setId={open.id}
          initialErrors={open.errors}
          onClose={() => setOpen(null)}
          onFinished={onFinished}
        />
      )}
    </section>
  );
}

/** A named RFI's status, size, price and findings: the list row, and the head of its page. */
function SetSummary({ set: s, title }: { set: RfiSetDto; title?: boolean }) {
  const status = setStatus(s.runs);
  const elements = s.items.length;
  const cost = usd(s.costLowUsd, s.costHighUsd);
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <div className="flex items-center gap-2">
        {title && <span className="truncate text-sm font-semibold">{s.name}</span>}
        <Badge variant={status.tone} className={cn("shrink-0", title && "ml-auto")}>{status.label}</Badge>
      </div>
      <span className="text-muted-foreground text-xs">
        {elements} element{elements === 1 ? "" : "s"} · {s.sheets.length} sheet{s.sheets.length === 1 ? "" : "s"}
        {cost ? ` · est. ${cost}` : ""}
      </span>
      <span className="text-xs">
        {s.findings ? (
          <>
            <strong>{s.findings}</strong> finding{s.findings === 1 ? "" : "s"}
            {s.pending ? `, ${s.pending} to review` : ""}
          </>
        ) : (
          <span className="text-muted-foreground">No findings yet</span>
        )}
      </span>
    </div>
  );
}

function CreateRfiDialog({
  projectId,
  onClose,
  onCreated,
}: {
  projectId: string;
  onClose: () => void;
  onCreated: (detail: RfiSetDetailDto) => void;
}) {
  const queryClient = useQueryClient();
  const library = useQuery({ queryKey: ["rfi-library", projectId], queryFn: () => api.rfiLibrary(projectId) });
  const sheets = useLibrarySheets(projectId);
  const [name, setName] = useState("");
  const [chosenSheets, setChosenSheets] = useState<string[]>([]);
  const [picks, setPicks] = useState<Picks>({});
  const [openPackage, setOpenPackage] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const packages = library.data?.packages ?? [];
  const elements = useMemo(() => packages.flatMap((p) => p.elements), [packages]);
  const chosen = Object.entries(picks).filter(([, ids]) => ids.length > 0);
  const questionCount = chosen.reduce((n, [, ids]) => n + ids.length, 0);
  // Sheets the chosen elements usually need, from their Compare Across words.
  const suggested = useMemo(() => {
    const all = new Set<string>();
    for (const [id] of chosen) {
      const pkg = packages.find((p) => p.elements.some((x) => x.id === id));
      const e = pkg?.elements.find((x) => x.id === id);
      if (e) suggestSheets(e.compareAcross, sheets, Infinity, e.name, pkg?.name).sheets.forEach((s) => all.add(s));
    }
    return [...all];
  }, [picks, elements, sheets]);

  const pickPackage = (packageId: string) => {
    const pkg = packages.find((p) => p.id === packageId);
    if (!pkg) return;
    if (!pkg.elements.some((e) => picks[e.id]?.length)) {
      // A card chosen for the first time takes every element and question under it.
      setPicks((prev) => ({ ...prev, ...Object.fromEntries(pkg.elements.map((e) => [e.id, allQuestions(e)])) }));
    }
    setOpenPackage(packageId);
  };

  const create = useMutation({
    mutationFn: async () => {
      const made = await api.createRfiSet(projectId, {
        name: name.trim(),
        sheets: chosenSheets,
        items: chosen.map(([elementId, questionIds]) => ({ elementId, questionIds })),
      });
      // Price it at once: one plan per element, no AI call. Start is a separate press.
      return api.planRfiSet(projectId, made.id);
    },
    onSuccess: (detail) => {
      queryClient.setQueryData(["rfi-set", projectId, detail.id], detail);
      void queryClient.invalidateQueries({ queryKey: ["rfi-sets", projectId] });
      onCreated(detail);
    },
  });

  // Search: which stage, element and question each match is in, without opening them.
  const hits = useMemo(() => searchLibrary(packages, query), [packages, query]);
  const toggleQuestion = (elementId: string, id: string, on: boolean) =>
    setPicks((prev) => {
      const had = prev[elementId] ?? [];
      return { ...prev, [elementId]: on ? [...new Set([...had, id])] : had.filter((x) => x !== id) };
    });

  const tooMany = chosen.length > RFI_SET_MAX_ELEMENTS;
  const ready = name.trim().length > 0 && chosenSheets.length > 0 && chosen.length > 0 && !tooMany;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="flex max-h-[92vh] flex-col gap-0 p-0 sm:max-w-5xl">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle className="text-xl">Create RFI</DialogTitle>
          <DialogDescription className="text-sm">
            Name it, pick the sheets to read, then the elements and questions to check. You see the price before
            anything is sent to the AI.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-7 overflow-y-auto px-6 py-5">
          <section className="flex flex-col gap-2">
            <label className="text-sm font-semibold" htmlFor="rfi-name">
              1 · Name
            </label>
            <Input
              id="rfi-name"
              autoFocus
              value={name}
              maxLength={RFI_SET_NAME_CHARS}
              placeholder="e.g. Level B2 foundations"
              className="h-10 text-base"
              onChange={(e) => setName(e.target.value)}
            />
          </section>

          <section className="flex flex-col gap-2">
            <p className="text-sm font-semibold">2 · Sheets</p>
            <SheetPicker
              projectId={projectId}
              sheets={sheets}
              value={chosenSheets}
              suggested={suggested}
              onChange={setChosenSheets}
              roomy
            />
          </section>

          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="text-sm font-semibold">3 · Elements</p>
              <span className="text-muted-foreground text-sm">
                Pick a stage to select all its elements and questions, then narrow it down.
              </span>
            </div>
            <SearchBox value={query} onChange={setQuery}
              placeholder="Search elements and questions, e.g. elevator pit, dowel, top of slab" />
            {library.isLoading ? (
              <Spinner />
            ) : library.error ? (
              <Notice tone="error">{(library.error as Error).message}</Notice>
            ) : query.trim() ? (
              <SearchResults
                hits={hits.out}
                more={hits.more}
                query={query}
                picks={picks}
                onElement={(e, on) => setPicks((prev) => ({ ...prev, [e.id]: on ? allQuestions(e) : [] }))}
                onQuestion={toggleQuestion}
                onOpenStage={setOpenPackage}
              />
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {packages.map((p) => {
                  const on = p.elements.filter((e) => picks[e.id]?.length);
                  const questions = on.reduce((n, e) => n + (picks[e.id]?.length ?? 0), 0);
                  return (
                    <div key={p.id} className="relative">
                      <button
                        type="button"
                        onClick={() => pickPackage(p.id)}
                        className={cn(
                          "flex min-h-24 w-full flex-col gap-1.5 rounded-xl border p-4 pr-12 text-left transition-colors",
                          on.length ? "border-primary bg-primary/5 ring-primary ring-1" : "bg-card hover:bg-accent/50",
                        )}
                      >
                        <span className="text-muted-foreground text-xs font-medium">Stage {p.id}</span>
                        <span className="text-base leading-snug font-semibold">{p.name}</span>
                        <span className="text-muted-foreground text-sm">
                          {on.length
                            ? `${on.length} of ${p.elements.length} elements · ${questions} questions`
                            : `${p.elements.length} elements`}
                        </span>
                      </button>
                      {/* Tick or untick the whole stage without opening it. */}
                      <AllBox
                        label=""
                        className="absolute top-3.5 right-3.5 [&_input]:size-4"
                        count={on.length}
                        total={p.elements.length}
                        onChange={(yes) => setPicks((prev) => ({ ...prev, ...setStage(p, yes) }))}
                      />
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>

        <DialogFooter className="flex-col items-stretch gap-2 border-t px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
          <span className={cn("text-sm", tooMany ? "text-destructive" : "text-muted-foreground")}>
            {chosen.length} element{chosen.length === 1 ? "" : "s"} · {questionCount} question{questionCount === 1 ? "" : "s"} ·{" "}
            {chosenSheets.length} sheet{chosenSheets.length === 1 ? "" : "s"}
            {tooMany ? ` · at most ${RFI_SET_MAX_ELEMENTS} elements in one RFI` : " · each element is its own review"}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button disabled={!ready || create.isPending} onClick={() => create.mutate()}>
              {create.isPending ? <Spinner /> : null}
              Create RFI and see the price
            </Button>
          </div>
        </DialogFooter>
        {create.error && (
          <div className="px-6 pb-4">
            <Notice tone="error">{(create.error as Error).message}</Notice>
          </div>
        )}

        {openPackage && library.data && (
          <PackageDialog
            projectId={projectId}
            library={library.data}
            packageId={openPackage}
            picks={picks}
            onPicks={setPicks}
            onClose={() => setOpenPackage(null)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Search hits grouped by stage: tick an element (all its questions) or one
 * question straight from the list; the stage name opens that stage. */
function SearchResults({
  hits,
  more,
  query,
  picks,
  onElement,
  onQuestion,
  onOpenStage,
}: {
  hits: Hit[];
  more: boolean;
  query: string;
  picks: Picks;
  onElement: (e: RfiLibraryElementDto, on: boolean) => void;
  onQuestion: (elementId: string, questionId: string, on: boolean) => void;
  /** Absent inside a stage's own window, where there is no stage to open. */
  onOpenStage?: (packageId: string) => void;
}) {
  if (!hits.length) {
    return <p className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm">Nothing matches “{query.trim()}”.</p>;
  }
  const stages = [...new Set(hits.map((h) => h.pkg.id))];
  return (
    <div className="flex flex-col gap-4">
      {stages.map((id) => {
        const inStage = hits.filter((h) => h.pkg.id === id);
        const pkg = inStage[0]!.pkg;
        return (
          <section key={id} className="rounded-xl border">
            {onOpenStage && <button type="button" onClick={() => onOpenStage(id)}
              className="bg-muted/40 hover:bg-muted flex w-full items-baseline gap-2 rounded-t-xl border-b px-4 py-2.5 text-left">
              <span className="text-muted-foreground text-xs font-medium">Stage {pkg.id}</span>
              <span className="text-sm font-semibold">{pkg.name}</span>
              <span className="text-muted-foreground ml-auto text-xs">Open stage</span>
            </button>}
            <ul className="flex flex-col divide-y">
              {inStage.map(({ element, questions }) => {
                const chosen = picks[element.id] ?? [];
                const total = flat(element.questions).length;
                return (
                  <li key={element.id} className="flex flex-col gap-1.5 px-4 py-3">
                    <AllBox
                      className="text-sm font-medium [&_input]:size-4"
                      label={`${element.name} (${chosen.length} of ${total} questions)`}
                      count={chosen.length}
                      total={total}
                      onChange={(on) => onElement(element, on)}
                    />
                    {questions.length > 0 && (
                      <ul className="flex flex-col gap-1 pl-6">
                        {questions.map((q) => (
                          <li key={q.id}>
                            <label className="flex cursor-pointer items-start gap-2 text-sm">
                              <Checkbox className="mt-0.5" checked={chosen.includes(q.id)}
                                onCheckedChange={(v) => onQuestion(element.id, q.id, v === true)} />
                              <span>{q.text}</span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
      {more && <p className="text-muted-foreground text-sm">More results. Add a word to narrow the search.</p>}
    </div>
  );
}

/** One stage's elements: include or leave out each, narrow its questions, add
 * questions, follow-ups and elements of the project's own. */
function PackageDialog({
  projectId,
  library,
  packageId,
  picks,
  onPicks,
  onClose,
}: {
  projectId: string;
  library: RfiLibraryDto;
  packageId: string;
  picks: Picks;
  onPicks: (update: (prev: Picks) => Picks) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const pkg = library.packages.find((p) => p.id === packageId)!;
  const [expanded, setExpanded] = useState<string | null>(null);
  const [newElement, setNewElement] = useState("");
  const [query, setQuery] = useState("");
  const hits = useMemo(() => searchLibrary([pkg], query), [pkg, query]);
  const setFor = (id: string, ids: string[]) => onPicks((prev) => ({ ...prev, [id]: ids }));

  const addElement = useMutation({
    mutationFn: () => api.addLibraryElement(projectId, { packageId, name: newElement.trim() }),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: ["rfi-library", projectId] });
      const fresh = queryClient.getQueryData<RfiLibraryDto>(["rfi-library", projectId]);
      const element = fresh?.packages.flatMap((p) => p.elements).find((e) => e.id === row.id);
      setFor(row.id, element ? allQuestions(element) : []);
      setExpanded(row.id);
      setNewElement("");
    },
  });
  const removeElement = useMutation({
    mutationFn: (id: string) => api.deleteLibraryElement(projectId, id),
    onSuccess: async (_r, id) => {
      onPicks((prev) => Object.fromEntries(Object.entries(prev).filter(([k]) => k !== id)));
      await queryClient.invalidateQueries({ queryKey: ["rfi-library", projectId] });
    },
  });

  const onCount = pkg.elements.filter((e) => picks[e.id]?.length).length;
  const allOn = onCount === pkg.elements.length;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="flex max-h-[88vh] flex-col gap-0 p-0 sm:max-w-3xl">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle className="text-lg">
            Stage {pkg.id} · {pkg.name}
          </DialogTitle>
          <DialogDescription className="text-sm">
            Untick what this RFI should not check. Open an element to choose its questions or add your own.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2 overflow-y-auto px-6 py-4">
          <SearchBox value={query} onChange={setQuery} placeholder={`Search ${pkg.name}: elements and questions`} />
          {query.trim() ? (
            <SearchResults
              hits={hits.out}
              more={hits.more}
              query={query}
              picks={picks}
              onElement={(e, on) => setFor(e.id, on ? allQuestions(e) : [])}
              onQuestion={(elementId, id, on) => {
                const had = picks[elementId] ?? [];
                setFor(elementId, on ? [...new Set([...had, id])] : had.filter((x) => x !== id));
              }}
            />
          ) : (
          <>
          <AllBox
            className="text-muted-foreground px-3 pb-1 text-sm font-medium [&_input]:size-4"
            label={`All elements (${onCount} of ${pkg.elements.length})`}
            count={onCount}
            total={pkg.elements.length}
            onChange={(on) => onPicks((prev) => ({ ...prev, ...setStage(pkg, on) }))}
          />
          {pkg.elements.map((e) => {
            const total = flat(e.questions).length;
            const on = picks[e.id]?.length ?? 0;
            const isOpen = expanded === e.id;
            return (
              <div key={e.id} className={cn("rounded-lg border", on ? "bg-card" : "bg-muted/30")}>
                <div className="flex items-center gap-3 p-3">
                  <Checkbox
                    checked={on > 0}
                    aria-label={`Include ${e.name}`}
                    onCheckedChange={(v) => setFor(e.id, v === true ? allQuestions(e) : [])}
                  />
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    onClick={() => setExpanded(isOpen ? null : e.id)}
                  >
                    {isOpen ? <ChevronDownIcon className="size-4 shrink-0" /> : <ChevronRightIcon className="size-4 shrink-0" />}
                    <span className="truncate text-sm font-medium">{e.name}</span>
                    {e.custom && <Badge variant="outline">yours</Badge>}
                    <span className="text-muted-foreground ml-auto shrink-0 text-xs">
                      {on} of {total} questions
                    </span>
                  </button>
                  {e.custom && (
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-destructive"
                      title="Delete this element and its questions"
                      disabled={removeElement.isPending}
                      onClick={() => removeElement.mutate(e.id)}
                    >
                      <Trash2Icon className="size-4" />
                    </button>
                  )}
                </div>
                {isOpen && (
                  <div className="border-t px-4 py-3">
                    <QuestionTree
                      projectId={projectId}
                      element={e}
                      selected={picks[e.id] ?? []}
                      onSelect={(ids) => setFor(e.id, ids.slice(0, QUESTIONS_PER_ELEMENT))}
                      roomy
                    />
                  </div>
                )}
              </div>
            );
          })}
          </>
          )}
          <div className="mt-2 flex gap-2">
            <Input
              value={newElement}
              maxLength={120}
              placeholder={`Add an element to ${pkg.name}`}
              className="h-9 text-sm"
              onChange={(e) => setNewElement(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && newElement.trim() && addElement.mutate()}
            />
            <Button variant="outline" disabled={!newElement.trim() || addElement.isPending} onClick={() => addElement.mutate()}>
              {addElement.isPending ? <Spinner /> : <PlusIcon />} Add element
            </Button>
          </div>
          {(addElement.error || removeElement.error) && (
            <Notice tone="error">{((addElement.error ?? removeElement.error) as Error).message}</Notice>
          )}
        </div>
        <DialogFooter className="border-t px-6 py-4 sm:justify-between">
          <Button variant="outline" onClick={() => onPicks((prev) => ({ ...prev, ...setStage(pkg, !allOn) }))}>
            {allOn ? "Unselect all" : "Select all"}
          </Button>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** One RFI: its reviews, their price, Start, and the findings they made. */
function RfiSetDialog({
  projectId,
  setId,
  initialErrors,
  onClose,
  onFinished,
}: {
  projectId: string;
  setId: string;
  initialErrors: RfiSetDetailDto["planErrors"];
  onClose: () => void;
  onFinished: () => void;
}) {
  const queryClient = useQueryClient();
  const [errors, setErrors] = useState(initialErrors);
  const detail = useQuery({
    queryKey: ["rfi-set", projectId, setId],
    queryFn: () => api.getRfiSet(projectId, setId),
    refetchInterval: (q) => (q.state.data?.reviews.some((r) => ACTIVE.includes(r.status)) ? 3000 : false),
  });
  const library = useQuery({ queryKey: ["rfi-library", projectId], queryFn: () => api.rfiLibrary(projectId) });
  const names = useMemo(
    () => new Map((library.data?.packages ?? []).flatMap((p) => p.elements).map((e) => [e.id, e.name])),
    [library.data],
  );

  const refresh = (d?: RfiSetDetailDto) => {
    if (d) {
      queryClient.setQueryData(["rfi-set", projectId, setId], d);
      setErrors(d.planErrors);
    }
    void queryClient.invalidateQueries({ queryKey: ["rfi-sets", projectId] });
  };
  const plan = useMutation({ mutationFn: () => api.planRfiSet(projectId, setId), onSuccess: refresh });
  const start = useMutation({ mutationFn: () => api.startRfiSet(projectId, setId), onSuccess: refresh });
  const remove = useMutation({
    mutationFn: () => api.deleteRfiSet(projectId, setId),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  // Reviews finishing refresh the project's lists too.
  const active = detail.data?.reviews.filter((r) => ACTIVE.includes(r.status)).length ?? 0;
  const wasActive = useRef(active);
  useEffect(() => {
    if (wasActive.current > 0 && active === 0) {
      refresh();
      onFinished();
    }
    wasActive.current = active;
  }, [active]);

  const d = detail.data;
  const planned = d?.reviews.filter((r) => r.status === "planned") ?? [];
  const plannedCost = usd(
    planned.every((r) => r.estimate?.costLowUsd != null) ? planned.reduce((n, r) => n + (r.estimate!.costLowUsd ?? 0), 0) : null,
    planned.every((r) => r.estimate?.costHighUsd != null) ? planned.reduce((n, r) => n + (r.estimate!.costHighUsd ?? 0), 0) : null,
  );
  const pending = d?.candidates.filter((c) => c.status === "pending") ?? [];
  const errorOf = (elementId: string) => errors.find((e) => e.elementId === elementId)?.error;
  const busy = plan.isPending || start.isPending || remove.isPending;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="flex max-h-[92vh] flex-col gap-0 p-0 sm:max-w-4xl">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle className="text-xl">{d?.name ?? "RFI"}</DialogTitle>
          <DialogDescription className="text-sm">
            {d ? `Sheets: ${d.sheets.join(", ")}` : "Loading…"}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-6 overflow-y-auto px-6 py-5">
          {detail.isLoading || !d ? (
            <Spinner />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                {planned.length > 0 && (
                  <Button disabled={busy} onClick={() => start.mutate()}>
                    {start.isPending ? <Spinner /> : <PlayIcon />}
                    Start {planned.length} review{planned.length === 1 ? "" : "s"}
                    {plannedCost ? ` · est. ${plannedCost}` : ""}
                  </Button>
                )}
                {active === 0 && (
                  <Button variant="outline" disabled={busy} onClick={() => plan.mutate()}>
                    {plan.isPending ? <Spinner /> : <RefreshCwIcon />}
                    {planned.length ? "Price again" : "Price it again"}
                  </Button>
                )}
                <Button variant="ghost" className="ml-auto" disabled={busy} onClick={() => remove.mutate()}>
                  <Trash2Icon /> Delete RFI
                </Button>
              </div>
              {planned.length > 0 && (
                <p className="text-muted-foreground -mt-3 text-sm">
                  Nothing has been sent to the AI yet. The price is a range because token counts are estimated.
                </p>
              )}
              {(plan.error || start.error || remove.error) && (
                <Notice tone="error">{((plan.error ?? start.error ?? remove.error) as Error).message}</Notice>
              )}

              <section className="flex flex-col gap-2">
                <h4 className="text-sm font-semibold">Elements ({d.items.length})</h4>
                <ul className="flex flex-col divide-y rounded-lg border">
                  {d.items.map((item) => {
                    const run = d.reviews.find((r) => r.library?.element.id === item.elementId);
                    const err = errorOf(item.elementId) ?? (run?.status === "failed" || run?.status === "stale" ? run.error : null);
                    const cost = run?.estimate ? usd(run.estimate.costLowUsd, run.estimate.costHighUsd) : null;
                    return (
                      <li key={item.elementId} className="flex flex-col gap-1.5 p-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium">{run?.library?.element.name ?? names.get(item.elementId) ?? item.elementId}</span>
                          <span className="text-muted-foreground text-xs">{item.questionIds.length} questions</span>
                          <span className="ml-auto flex items-center gap-2 text-xs">
                            {cost && run?.status === "planned" && <span className="text-muted-foreground">est. {cost}</span>}
                            {run && run.candidates > 0 && <span>{run.candidates} found</span>}
                            <Badge variant={run ? (run.status === "failed" ? "destructive" : "outline") : "secondary"}>
                              {run ? REVIEW_LABEL[run.status] : "Not priced"}
                            </Badge>
                          </span>
                        </div>
                        {run && ACTIVE.includes(run.status) && <Progress value={run.progress} className="h-1.5" />}
                        {err && <span className="text-destructive text-xs">{err}</span>}
                      </li>
                    );
                  })}
                </ul>
              </section>

              <section className="flex flex-col gap-2">
                <h4 className="text-sm font-semibold">
                  Findings {pending.length > 0 && <span className="text-muted-foreground font-normal">({pending.length} to review)</span>}
                </h4>
                <SetFindings projectId={projectId} setId={setId} onFinished={onFinished} />
              </section>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * One Create RFI's findings waiting for a decision, with Accept and Dismiss.
 * Shown in the card's slider and in its full view; both read the same cached
 * set, so a decision in one shows in the other.
 */
function SetFindings({ projectId, setId, onFinished }: { projectId: string; setId: string; onFinished: () => void }) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["rfi-set", projectId, setId],
    queryFn: () => api.getRfiSet(projectId, setId),
    refetchInterval: (q) => (q.state.data?.reviews.some((r) => ACTIVE.includes(r.status)) ? 3000 : false),
  });
  const decided = () => {
    void queryClient.invalidateQueries({ queryKey: ["rfi-set", projectId, setId] });
    void queryClient.invalidateQueries({ queryKey: ["rfi-sets", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["rfi-candidates", projectId] });
    onFinished();
  };
  const accept = useMutation({ mutationFn: (id: string) => api.acceptRfiCandidate(projectId, id), onSuccess: decided });
  const dismiss = useMutation({ mutationFn: (id: string) => api.dismissRfiCandidate(projectId, id), onSuccess: decided });

  const d = detail.data;
  if (!d) return detail.error ? <Notice tone="error">{(detail.error as Error).message}</Notice> : <Spinner />;
  const pending = d.candidates.filter((c) => c.status === "pending");
  const decidedCount = d.candidates.length - pending.length;
  const active = d.reviews.some((r) => ACTIVE.includes(r.status));
  return (
    <div className="flex flex-col gap-2">
      {pending.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          {active
            ? "Reviews are running. Findings appear here as each one finishes."
            : d.candidates.length
              ? "Every finding has been decided."
              : d.reviews.some((r) => r.status === "planned")
                ? "Not started yet. Open the full view to see the price and start it."
                : "No findings yet."}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {pending.map((c) => (
            <CandidateCard
              key={c.id}
              projectId={projectId}
              candidate={c}
              busy={accept.isPending || dismiss.isPending}
              onAccept={() => accept.mutate(c.id)}
              onDismiss={() => dismiss.mutate(c.id)}
            />
          ))}
        </ul>
      )}
      {decidedCount > 0 && (
        <p className="text-muted-foreground text-xs">{decidedCount} already decided. Accepted ones are in the RFI log.</p>
      )}
      {(accept.error || dismiss.error) && <Notice tone="error">{((accept.error ?? dismiss.error) as Error).message}</Notice>}
    </div>
  );
}
