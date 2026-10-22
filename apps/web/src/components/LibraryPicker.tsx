import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightIcon, CornerDownRightIcon, PlusIcon, SearchIcon, Trash2Icon, XIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  RFI_CHECK_LABELS,
  RFI_CUSTOM_QUESTION_CHARS,
  libraryCatalogueIds,
  sheetTitleFrom,
  suggestSheets,
  type LibrarySheet,
  type RfiLibraryElementDto,
  type RfiLibraryQuestionDto,
  type RfiReviewCheckId,
} from "@cdip/shared";
import { api } from "@/api";
import { Notice, Spinner } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";

/** What a person picked in the library: one element, its questions, the sheets to read. */
export interface LibraryChoice {
  elementId: string | null;
  questionIds: string[];
  sheets: string[];
}

const KEY = (projectId: string) => `cdip-rfi-library:${projectId}`;
const EMPTY: LibraryChoice = { elementId: null, questionIds: [], sheets: [] };

/** Remembered per project in this browser, like the scan's scope. */
export function loadChoice(projectId: string): LibraryChoice {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY(projectId)) ?? "null") as Partial<LibraryChoice> | null;
    return raw ? { ...EMPTY, ...raw } : EMPTY;
  } catch {
    return EMPTY;
  }
}
function saveChoice(projectId: string, choice: LibraryChoice) {
  try {
    localStorage.setItem(KEY(projectId), JSON.stringify(choice));
  } catch {
    // Private window or blocked storage: the choice simply is not remembered.
  }
}

export const flat = (qs: RfiLibraryQuestionDto[]): RfiLibraryQuestionDto[] => qs.flatMap((q) => [q, ...flat(q.children)]);

/** What the review can ask today for this choice, and what is ready to plan. */
export function choiceSummary(element: RfiLibraryElementDto | undefined, choice: LibraryChoice) {
  const chosen = element ? flat(element.questions).filter((q) => choice.questionIds.includes(q.id)) : [];
  const checkIds: RfiReviewCheckId[] = element ? libraryCatalogueIds(element, chosen) : [];
  return { chosen, checkIds, ready: !!element && chosen.length > 0 && choice.sheets.length > 0 };
}

/** Every sheet of the project once, with what the picker needs to suggest from. */
export function useLibrarySheets(projectId: string): LibrarySheet[] {
  const manifest = useQuery({ queryKey: ["manifest", projectId], queryFn: () => api.manifest(projectId) });
  const headings = useQuery({ queryKey: ["rfi-library-headings", projectId], queryFn: () => api.rfiLibraryHeadings(projectId) });
  const titles = useQuery({ queryKey: ["rfi-library-titles", projectId], queryFn: () => api.rfiLibraryTitles(projectId) });
  return useMemo<LibrarySheet[]>(() => {
    const seen = new Set<string>();
    return (manifest.data ?? []).flatMap((p) =>
      p.sheetNumber && !seen.has(p.sheetNumber) && seen.add(p.sheetNumber)
        ? [{
            combinedPageNumber: p.combinedPageNumber, sheetNumber: p.sheetNumber, discipline: p.discipline,
            sheetKind: p.sheetKind ?? null, level: p.level ?? null, headings: headings.data?.[p.combinedPageNumber] ?? [],
            titleBlock: titles.data?.[p.combinedPageNumber],
          }]
        : [],
    );
  }, [manifest.data, headings.data, titles.data]);
}

/**
 * The structured RFI flow (docs/rfi-structured-review-requirements.md, R3):
 * package → element (grids first, the client's review order) → its questions,
 * the built-in ones and the project's own → the sheets to read. The planner,
 * price and results that follow are the targeted review's own.
 */
export function LibraryPicker({
  projectId,
  value,
  onChange,
}: {
  projectId: string;
  value: LibraryChoice;
  onChange: (choice: LibraryChoice) => void;
}) {
  const library = useQuery({ queryKey: ["rfi-library", projectId], queryFn: () => api.rfiLibrary(projectId) });
  const set = (patch: Partial<LibraryChoice>) => {
    const next = { ...value, ...patch };
    saveChoice(projectId, next);
    onChange(next);
  };

  const elements = useMemo(() => library.data?.packages.flatMap((p) => p.elements) ?? [], [library.data]);
  const element = elements.find((e) => e.id === value.elementId);
  const pkg = library.data?.packages.find((p) => p.elements.some((e) => e.id === value.elementId));
  const sheets = useLibrarySheets(projectId);
  const suggestion = element ? suggestSheets(element.compareAcross, sheets, Infinity, element.name, pkg?.name) : null;

  const pickElement = (id: string) => {
    const next = elements.find((e) => e.id === id);
    if (!next) return;
    // Every built-in question of the element to start with; the person narrows it.
    const questionIds = next.questions.filter((q) => q.source === "library").map((q) => q.id);
    set({ elementId: id, questionIds, sheets: value.sheets.length ? value.sheets : suggestSheets(next.compareAcross, sheets, Infinity, next.name, library.data?.packages.find((p) => p.elements.includes(next))?.name).sheets });
  };
  // A choice remembered from an element the library no longer has starts over.
  useEffect(() => {
    if (library.data && value.elementId && !element) set(EMPTY);
  }, [library.data]);

  if (library.isLoading) return <Spinner />;
  if (library.error) return <Notice tone="error">{(library.error as Error).message}</Notice>;
  const nextElement = element ? elements[elements.indexOf(element) + 1] : undefined;
  const { chosen, checkIds } = choiceSummary(element, value);
  const codeChecks = [...new Set(chosen.flatMap((q) => q.codeChecks))].map((t) => (RFI_CHECK_LABELS as Record<string, string>)[t] ?? t);

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-1.5">
        <p className="text-xs font-medium">1 · Element</p>
        <Select value={value.elementId ?? ""} onValueChange={pickElement}>
          <SelectTrigger size="sm" className="w-full text-xs" aria-label="Element">
            <SelectValue placeholder="Start with Grid lines, then work down" />
          </SelectTrigger>
          <SelectContent>
            {library.data!.packages.map((p) => (
              <SelectGroup key={p.id}>
                <SelectLabel className="text-[11px]">
                  {p.id} {p.name}
                </SelectLabel>
                {p.elements.map((e) => (
                  <SelectItem key={e.id} value={e.id} className="text-xs">
                    {e.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            ))}
          </SelectContent>
        </Select>
        {element && (
          <div className="text-muted-foreground flex flex-col gap-0.5 text-[11px] leading-relaxed">
            <span>
              {pkg?.name} · {element.id}
              {element.variants ? ` · ${element.variants}` : ""}
            </span>
            {element.attributes && <span>Checks: {element.attributes}</span>}
            {nextElement && (
              <button
                type="button"
                className="text-foreground flex items-center gap-1 self-start hover:underline"
                onClick={() => pickElement(nextElement.id)}
              >
                Next element: {nextElement.name} <ArrowRightIcon className="size-3" />
              </button>
            )}
          </div>
        )}
      </section>

      {element && (
        <QuestionTree
          projectId={projectId}
          element={element}
          selected={value.questionIds}
          onSelect={(questionIds) => set({ questionIds })}
        />
      )}

      {element && (
        <SheetPicker
          projectId={projectId}
          sheets={sheets}
          value={value.sheets}
          suggested={suggestion?.sheets ?? []}
          onChange={(next) => set({ sheets: next })}
        />
      )}

      {element && (suggestion?.missing.length || suggestion?.outside.length) ? (
        <div className="text-warning flex flex-col gap-0.5 text-[11px] leading-relaxed">
          {!!suggestion?.missing.length && <span>No sheets in this project for: {suggestion.missing.join(", ")}.</span>}
          {!!suggestion?.outside.length && (
            <span>
              Some answers need documents that are not drawings ({suggestion.outside.join(", ")}). Questions that depend
              on them come back "not shown", never "no issue".
            </span>
          )}
        </div>
      ) : null}

      {element && chosen.length > 0 && (
        <p className="text-muted-foreground text-[11px] leading-relaxed">
          The review asks your {chosen.length} chosen question{chosen.length === 1 ? "" : "s"} in these words
          {checkIds.length ? `, with the original question${checkIds.length === 1 ? "" : "s"} ${checkIds.join(", ")} for their measured checks` : ""}
          {codeChecks.length ? `. Code checks run first, free: ${codeChecks.join(", ")}` : ""}. An answer from the AI alone is never
          above medium confidence.
        </p>
      )}
    </div>
  );
}

/** One checkbox for a whole list: ticked when all are chosen, a dash when
 * some are; clicking selects all, or clears all when all are chosen. */
export function AllBox({
  count,
  total,
  onChange,
  label = "Select all",
  className = "",
}: {
  count: number;
  total: number;
  onChange: (on: boolean) => void;
  label?: string;
  className?: string;
}) {
  const state = total > 0 && count === total ? true : count > 0 ? "indeterminate" : false;
  return (
    <label className={`flex cursor-pointer items-center gap-1.5 select-none ${className}`}>
      <Checkbox checked={state} disabled={!total} onCheckedChange={() => onChange(count < total)} />
      {label}
    </label>
  );
}

export function QuestionTree({
  projectId,
  element,
  selected,
  onSelect,
  roomy = false,
}: {
  projectId: string;
  element: RfiLibraryElementDto;
  selected: string[];
  onSelect: (ids: string[]) => void;
  /** Larger text, for the Create RFI dialog. */
  roomy?: boolean;
}) {
  const t = roomy ? "text-sm" : "text-xs";
  const small = roomy ? "text-xs" : "text-[11px]";
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState<string | null>(null); // a parent id, or "" for a new top-level question
  const [text, setText] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["rfi-library", projectId] });
  const add = useMutation({
    mutationFn: () => api.addLibraryQuestion(projectId, { elementId: element.id, parentId: adding || null, text: text.trim() }),
    onSuccess: async (row) => {
      await refresh();
      onSelect([...selected, row.id]);
      setAdding(null);
      setText("");
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteLibraryQuestion(projectId, id),
    onSuccess: async ({ deleted }) => {
      await refresh();
      onSelect(selected.filter((id) => !deleted.includes(id)));
    },
  });

  const all = flat(element.questions);
  const toggle = (ids: string[], on: boolean) =>
    onSelect(on ? [...new Set([...selected, ...ids])] : selected.filter((id) => !ids.includes(id)));
  // Top-level questions grouped by the client's category; the project's own
  // with no category under one heading, universal ones last.
  const groups = new Map<string, RfiLibraryQuestionDto[]>();
  for (const q of element.questions) {
    const key = q.source === "universal" ? "Asked of every element" : (q.category ?? "Your questions");
    groups.set(key, [...(groups.get(key) ?? []), q]);
  }
  const ordered = [...groups].sort(([a], [b]) => Number(a === "Asked of every element") - Number(b === "Asked of every element"));

  const addBox = (parentId: string) =>
    adding === parentId && (
      <div className="mt-1 flex gap-1.5">
        <Input
          autoFocus
          value={text}
          maxLength={RFI_CUSTOM_QUESTION_CHARS}
          placeholder={parentId ? "Follow-up question" : `Your own question about ${element.name}`}
          className={`h-8 ${t}`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && text.trim()) add.mutate();
            if (e.key === "Escape") setAdding(null);
          }}
        />
        <Button size="sm" className="h-7" disabled={!text.trim() || add.isPending} onClick={() => add.mutate()}>
          {add.isPending ? <Spinner /> : "Add"}
        </Button>
        <Button size="sm" variant="ghost" className="h-7" onClick={() => setAdding(null)} aria-label="Cancel">
          <XIcon />
        </Button>
      </div>
    );

  const row = (q: RfiLibraryQuestionDto, depth: number): React.ReactNode => (
    <li key={q.id} style={{ paddingLeft: depth ? 16 : 0 }}>
      <div className="group flex items-start gap-2">
        {depth > 0 && <CornerDownRightIcon className="text-muted-foreground mt-0.5 size-3 shrink-0" />}
        <label className="flex min-w-0 flex-1 items-start gap-2">
          <Checkbox
            className="mt-0.5"
            checked={selected.includes(q.id)}
            onCheckedChange={(v) => toggle([q.id], v === true)}
          />
          <span className="min-w-0">
            {q.text}
            <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
              {q.source === "custom" && <Badge variant="outline" className="h-4 px-1 text-[10px]">yours</Badge>}
              {q.catalogue.map((id) => (
                <Badge key={id} variant="secondary" className="h-4 px-1 text-[10px]">
                  {id}
                </Badge>
              ))}
              {q.codeChecks.length > 0 && (
                <Badge variant="outline" className="h-4 px-1 text-[10px]" title={q.codeChecks.join(", ")}>
                  code check
                </Badge>
              )}
            </span>
          </span>
        </label>
        <button
          type="button"
          className="text-muted-foreground hover:text-foreground shrink-0 text-[11px]"
          title="Add a follow-up question under this one"
          onClick={() => {
            setAdding(q.id);
            setText("");
          }}
        >
          <PlusIcon className="size-3" />
        </button>
        {q.source === "custom" && (
          <button
            type="button"
            className="text-muted-foreground hover:text-destructive shrink-0"
            title="Delete this question and its follow-ups"
            disabled={remove.isPending}
            onClick={() => remove.mutate(q.id)}
          >
            <Trash2Icon className="size-3" />
          </button>
        )}
      </div>
      {addBox(q.id)}
      {q.children.length > 0 && <ul className="mt-1 flex flex-col gap-1">{q.children.map((c) => row(c, depth + 1))}</ul>}
    </li>
  );

  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <p className={`${t} font-medium`}>
          {roomy ? "Questions" : "2 · Questions"} <span className="text-muted-foreground font-normal">({selected.filter((id) => all.some((q) => q.id === id)).length} of {all.length})</span>
        </p>
        <AllBox
          className={small}
          count={selected.filter((id) => all.some((q) => q.id === id)).length}
          total={all.length}
          onChange={(on) => toggle(all.map((q) => q.id), on)}
        />
      </div>
      <div className={`flex flex-col gap-3 overflow-y-auto pr-1 ${t} ${roomy ? "" : "max-h-96"}`}>
        {ordered.map(([group, qs]) => (
          <div key={group}>
            <button
              type="button"
              className={`text-muted-foreground mb-1 ${small} font-medium tracking-wide uppercase hover:underline`}
              title="Select every question in this group"
              onClick={() => toggle(flat(qs).map((q) => q.id), !flat(qs).every((q) => selected.includes(q.id)))}
            >
              {group}
            </button>
            <ul className="flex flex-col gap-1.5">{qs.map((q) => row(q, 0))}</ul>
          </div>
        ))}
      </div>
      {adding === "" ? (
        addBox("")
      ) : (
        <button
          type="button"
          className={`text-primary flex items-center gap-1 self-start ${t} hover:underline`}
          onClick={() => {
            setAdding("");
            setText("");
          }}
        >
          <PlusIcon className="size-3" /> Add your own question
        </button>
      )}
      {(add.error || remove.error) && <Notice tone="error">{((add.error ?? remove.error) as Error).message}</Notice>}
    </section>
  );
}

/** Most sheets listed under a search at once. */
const SHEET_HITS = 40;

export function SheetPicker({
  projectId,
  sheets,
  value,
  suggested,
  onChange,
  roomy = false,
  heading,
}: {
  projectId: string;
  sheets: LibrarySheet[];
  value: string[];
  suggested: string[];
  onChange: (sheets: string[]) => void;
  /** Larger text and thumbnails, for the Create RFI dialog. */
  roomy?: boolean;
  heading?: string;
}) {
  const t = roomy ? "text-sm" : "text-xs";
  const [typed, setTyped] = useState("");
  const bySheet = new Map(sheets.map((s) => [s.sheetNumber.toUpperCase(), s]));
  const match = bySheet.get(typed.trim().toUpperCase());
  // Search by sheet number, discipline, kind, level or a drawing title on the sheet.
  const words = typed.toLowerCase().split(/\s+/).filter(Boolean);
  const hits = words.length
    ? sheets.filter((s) => {
        const hay = [s.sheetNumber, s.discipline, s.sheetKind, s.level, s.titleBlock, ...(s.headings ?? [])].filter(Boolean).join(" ").toLowerCase();
        return words.every((w) => hay.includes(w));
      })
    : [];
  const toggleSheet = (sheetNumber: string) =>
    onChange(value.includes(sheetNumber) ? value.filter((v) => v !== sheetNumber) : [...value, sheetNumber]);
  const addTyped = () => {
    const pick = match ?? (hits.length === 1 ? hits[0] : undefined);
    if (!pick || value.includes(pick.sheetNumber)) return;
    onChange([...value, pick.sheetNumber]);
    setTyped("");
  };
  const sameAsSuggested = suggested.length > 0 && suggested.every((s) => value.includes(s)) && value.length === suggested.length;

  return (
    <section className="flex min-w-0 flex-col gap-2">
      <p className={`${t} font-medium`}>
        {heading ?? (roomy ? "Sheets to read" : "3 · Sheets to read")} <span className="text-muted-foreground font-normal">(as many as you need)</span>
      </p>
      {value.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {value.map((sheetNumber) => {
            const s = bySheet.get(sheetNumber.toUpperCase());
            return (
              <li key={sheetNumber} className={`bg-card flex items-center gap-1.5 rounded-md border p-1 pr-1.5 ${t}`}>
                {s && (
                  <img
                    src={api.pageThumbUrl(projectId, s.combinedPageNumber)}
                    alt=""
                    className={`${roomy ? "h-12 w-16" : "h-9 w-12"} rounded-sm border object-cover`}
                    loading="lazy"
                  />
                )}
                <span className="font-medium">{sheetNumber}</span>
                <button
                  type="button"
                  className="text-muted-foreground hover:text-destructive"
                  aria-label={`Remove ${sheetNumber}`}
                  onClick={() => onChange(value.filter((v) => v !== sheetNumber))}
                >
                  <XIcon className="size-3" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="relative">
        <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
        <Input
          value={typed}
          placeholder="Search by sheet number, discipline, level or title (e.g. S2.105, foundation plan, level 2)"
          className={`${roomy ? "h-10" : "h-8"} pl-8 ${t}`}
          aria-label="Search sheets"
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") addTyped();
            if (e.key === "Escape" && typed) (e.stopPropagation(), setTyped(""));
          }}
        />
        {typed && (
          <button type="button" aria-label="Clear search" onClick={() => setTyped("")}
            className="text-muted-foreground hover:text-foreground absolute top-1/2 right-2.5 -translate-y-1/2">
            <XIcon className="size-3.5" />
          </button>
        )}
      </div>
      {words.length > 0 &&
        (hits.length ? (
          <ul className={`flex max-h-72 flex-col divide-y overflow-y-auto rounded-md border ${t}`}>
            {hits.slice(0, SHEET_HITS).map((s) => {
              const on = value.includes(s.sheetNumber);
              return (
                <li key={s.sheetNumber}>
                  <label className={`hover:bg-accent/50 flex cursor-pointer items-center gap-2.5 px-2.5 py-1.5 ${on ? "bg-primary/5" : ""}`}>
                    <Checkbox checked={on} onCheckedChange={() => toggleSheet(s.sheetNumber)} />
                    <img src={api.pageThumbUrl(projectId, s.combinedPageNumber)} alt="" loading="lazy"
                      className={`${roomy ? "h-10 w-14" : "h-8 w-11"} shrink-0 rounded-sm border object-cover`} />
                    <span className="flex min-w-0 flex-col">
                      <span className="font-medium">
                        {s.sheetNumber}
                        <span className="text-muted-foreground ml-2 font-normal">
                          {[s.discipline, s.sheetKind, s.level].filter(Boolean).join(" · ")}
                        </span>
                      </span>
                      {(s.titleBlock || s.headings?.[0]) && (
                        <span className="text-muted-foreground truncate text-xs">
                          {s.titleBlock ? sheetTitleFrom(s.titleBlock, s.sheetNumber) : s.headings!.slice(0, 2).join(" · ")}
                        </span>
                      )}
                    </span>
                  </label>
                </li>
              );
            })}
            {hits.length > SHEET_HITS && (
              <li className="text-muted-foreground px-2.5 py-1.5 text-xs">{hits.length - SHEET_HITS} more. Add a word to narrow it.</li>
            )}
          </ul>
        ) : (
          <span className="text-muted-foreground text-[11px]">No sheet matches “{typed.trim()}”.</span>
        ))}
      {suggested.length > 0 && !sameAsSuggested && (
        <button
          type="button"
          className="text-primary self-start text-[11px] hover:underline"
          onClick={() => onChange(suggested)}
        >
          Use the suggested sheets: {suggested.join(", ")}
        </button>
      )}
    </section>
  );
}
