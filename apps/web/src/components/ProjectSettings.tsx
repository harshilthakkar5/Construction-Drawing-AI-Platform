import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SaveIcon, SettingsIcon } from "lucide-react";
import { useState } from "react";
import {
  RFI_REVIEW_DEPTH_KEYS,
  RFI_REVIEW_DEPTHS,
  RFI_REVIEW_INPUT_LIMITS,
  RFI_REVIEW_THINKING,
  RFI_REVIEW_THINKING_LIMITS,
  rfiSettingsOf,
  type ProjectDto,
  type RfiProjectSettings,
  type RfiReviewDepth,
  type RfiReviewProvider,
  type RfiReviewThinking,
} from "@cdip/shared";
import { api } from "@/api";
import { RolesSelect } from "@/components/RolesSelect";
import { Notice, Spinner, TextField } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAppStore } from "@/store";

const THINKING_HINT: Record<RfiReviewThinking, string> = {
  low: "Fastest and cheapest.",
  medium: "The default: more careful on crowded sheets.",
  high: "Most careful; slower and costs more.",
};

/** The project row, shared with every other screen that reads it. */
export function useProject(projectId: string) {
  return useQuery({
    queryKey: ["projects", projectId],
    queryFn: async () => (await api.listProjects()).find((p) => p.id === projectId),
  });
}

/** The project's RFI settings with the defaults filled in. */
export function useRfiSettings(projectId: string): RfiProjectSettings {
  return rfiSettingsOf(useProject(projectId).data?.rfiSettings);
}

/** One line in the RFIs tab naming the settings a run will use, with the way to change them. */
export function SettingsLine({ children }: { children: React.ReactNode }) {
  const setWorkTab = useAppStore((s) => s.setWorkTab);
  return (
    <p className="text-muted-foreground flex flex-wrap items-center gap-x-1 text-xs">
      <SettingsIcon className="size-3" />
      <span>{children}</span>
      <button type="button" className="text-foreground underline-offset-2 hover:underline" onClick={() => setWorkTab("settings")}>
        Change in Settings
      </button>
    </p>
  );
}

const DEFAULT = "default";
const modelKey = (provider: RfiReviewProvider | null, model: string | null) => (provider && model ? `${provider}|${model}` : DEFAULT);
const fromKey = (key: string) => {
  if (key === DEFAULT) return { provider: null, model: null };
  const [provider, model] = key.split("|") as [RfiReviewProvider, string];
  return { provider, model };
};
const num = (s: string) => (s.trim() ? Number(s) : null);
const text = (n: number | null) => (n === null ? "" : String(n));

interface Draft {
  name: string;
  description: string;
  roles: string[];
  review: RfiProjectSettings["review"];
  fullScan: RfiProjectSettings["fullScan"];
  maxInput: string;
  maxThinking: string;
  budget: string;
  autoUsd: string;
}

function draftOf(p: ProjectDto): Draft {
  const s = rfiSettingsOf(p.rfiSettings);
  return {
    name: p.name,
    description: p.description ?? "",
    roles: p.roles,
    review: s.review,
    fullScan: s.fullScan,
    maxInput: text(s.review.maxInputTokens),
    maxThinking: text(s.review.maxThinkingTokens),
    budget: text(s.fullScan.budgetUsd),
    autoUsd: text(p.rfiAutoScanUsd ?? null),
  };
}

const between = (s: string, min: number, max: number) => {
  const n = num(s);
  return n === null || (Number.isInteger(n) && n >= min && n <= max);
};
const dollars = (s: string, max: number) => {
  const n = num(s);
  return n === null || (Number.isFinite(n) && n > 0 && n <= max);
};

/**
 * Project settings: the project's details and every RFI setting that used to
 * sit in the RFIs tab beside the work. Saved on the project (owner only), so
 * everyone on it runs reviews the same way; the RFIs tab shows one line
 * naming what a run will use.
 */
export function ProjectSettings({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const project = useProject(projectId);
  const options = useQuery({ queryKey: ["rfi-review-options", projectId], queryFn: () => api.rfiReviewOptions(projectId) });
  // 404s where the server has the AI comparison turned off: no section for it.
  const fullScan = useQuery({ queryKey: ["rfi-full-scans", projectId], queryFn: () => api.listRfiFullScans(projectId), retry: false });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: (d: Draft) =>
      api.updateProject(projectId, {
        name: d.name.trim(),
        description: d.description.trim() || null,
        roles: d.roles,
        rfiAutoScanUsd: num(d.autoUsd),
        rfiSettings: {
          review: { ...d.review, maxInputTokens: num(d.maxInput), maxThinkingTokens: num(d.maxThinking) },
          fullScan: { ...d.fullScan, budgetUsd: num(d.budget) },
        },
      }),
    onSuccess: (p) => {
      queryClient.setQueryData(["projects", projectId], p);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      setDraft(null);
      setSaved(true);
    },
  });

  if (!project.data) return <div className="px-4">{project.isLoading ? <Spinner /> : <Notice tone="error">Project not found.</Notice>}</div>;
  const d = draft ?? draftOf(project.data);
  const set = (patch: Partial<Draft>) => {
    setDraft({ ...d, ...patch });
    setSaved(false);
  };
  const reviewModel = (options.data?.options ?? []).find((o) => modelKey(o.provider, o.model) === modelKey(d.review.provider, d.review.model));
  const takesBudget = d.review.provider ? !!reviewModel?.capability.budget : true;
  const valid =
    d.name.trim().length > 0 &&
    between(d.maxInput, RFI_REVIEW_INPUT_LIMITS.min, RFI_REVIEW_INPUT_LIMITS.max) &&
    between(d.maxThinking, RFI_REVIEW_THINKING_LIMITS.min, RFI_REVIEW_THINKING_LIMITS.max) &&
    dollars(d.budget, 10_000) &&
    dollars(d.autoUsd, 1000);

  const models = (needPrice: boolean) => (
    <>
      <SelectItem value={DEFAULT} className="text-xs">
        Server default{options.data ? ` (${options.data.default.model})` : ""}
      </SelectItem>
      {(options.data?.options ?? []).map((o) => (
        <SelectItem
          key={modelKey(o.provider, o.model)}
          value={modelKey(o.provider, o.model)}
          disabled={!o.available || (needPrice && !o.priced)}
          className="text-xs"
        >
          {o.provider === "gemini" ? "Gemini" : "Claude"} · {o.model}
          {!o.available ? " (no API key)" : !o.priced ? " (price unknown)" : ""}
        </SelectItem>
      ))}
    </>
  );

  return (
    <div className="flex flex-col gap-5 px-4 pb-2">
      <section className="flex flex-col gap-3">
        <h3 className="text-sm font-semibold">Project</h3>
        <TextField label="Name" value={d.name} onChange={(name) => set({ name })} required />
        <div className="grid gap-2">
          <Label htmlFor="project-description">Description</Label>
          <Textarea
            id="project-description"
            rows={3}
            maxLength={2000}
            value={d.description}
            onChange={(e) => set({ description: e.target.value })}
          />
        </div>
        <RolesSelect value={d.roles} onChange={(roles) => set({ roles })} />
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <div>
          <h3 className="text-sm font-semibold">Targeted RFI review</h3>
          <p className="text-muted-foreground text-xs">Used for every review planned in the RFIs tab.</p>
        </div>
        <Field label="AI model">
          <Select
            value={modelKey(d.review.provider, d.review.model)}
            onValueChange={(key) => set({ review: { ...d.review, ...fromKey(key) } })}
          >
            <SelectTrigger size="sm" className="w-full text-xs" aria-label="Review AI model">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>{models(false)}</SelectContent>
          </Select>
        </Field>
        <Field
          label="Depth"
          hint={`Up to ${RFI_REVIEW_DEPTHS[d.review.depth].visualPages} rendered pages, ${RFI_REVIEW_DEPTHS[d.review.depth].chunks} text pieces, ${RFI_REVIEW_DEPTHS[d.review.depth].maxBatches} reading call(s), ${RFI_REVIEW_DEPTHS[d.review.depth].maxTotalTokens.toLocaleString()} tokens in all.`}
        >
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            className="w-full"
            value={d.review.depth}
            onValueChange={(v) => v && set({ review: { ...d.review, depth: v as RfiReviewDepth } })}
            aria-label="Depth"
          >
            {RFI_REVIEW_DEPTH_KEYS.map((key) => (
              <ToggleGroupItem key={key} value={key} className="flex-1 text-xs">
                {RFI_REVIEW_DEPTHS[key].label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </Field>
        <Field label="How careful" hint={THINKING_HINT[d.review.thinking]}>
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            className="w-full"
            value={d.review.thinking}
            onValueChange={(v) => v && set({ review: { ...d.review, thinking: v as RfiReviewThinking } })}
            aria-label="How careful"
          >
            {RFI_REVIEW_THINKING.map((level) => (
              <ToggleGroupItem key={level} value={level} className="flex-1 text-xs capitalize">
                {level}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </Field>
        <NumberField
          label="Most input tokens per reading call"
          value={d.maxInput}
          placeholder={String(RFI_REVIEW_INPUT_LIMITS.default)}
          ok={between(d.maxInput, RFI_REVIEW_INPUT_LIMITS.min, RFI_REVIEW_INPUT_LIMITS.max)}
          hint={`${RFI_REVIEW_INPUT_LIMITS.min.toLocaleString()} – ${RFI_REVIEW_INPUT_LIMITS.max.toLocaleString()}. Smaller splits the reading into more calls; what does not fit is reported as not read.`}
          onChange={(maxInput) => set({ maxInput })}
        />
        <NumberField
          label="Most thinking tokens per call"
          value={d.maxThinking}
          placeholder="set by How careful"
          ok={between(d.maxThinking, RFI_REVIEW_THINKING_LIMITS.min, RFI_REVIEW_THINKING_LIMITS.max)}
          hint={
            takesBudget
              ? `${RFI_REVIEW_THINKING_LIMITS.min.toLocaleString()} – ${RFI_REVIEW_THINKING_LIMITS.max.toLocaleString()}. Used only by models that take a thinking budget.`
              : (reviewModel?.capability.note ?? "This model does not take a thinking budget; it is ignored.")
          }
          onChange={(maxThinking) => set({ maxThinking })}
        />
      </section>

      {fullScan.isSuccess && (
        <>
          <Separator />
          <section className="flex flex-col gap-3">
            <div>
              <h3 className="text-sm font-semibold">AI sheet comparison (step 2 of a scan)</h3>
              <p className="text-muted-foreground text-xs">Used when a scan's AI comparison is started.</p>
            </div>
            <Field label="AI model">
              <Select
                value={modelKey(d.fullScan.provider, d.fullScan.model)}
                onValueChange={(key) => set({ fullScan: { ...d.fullScan, ...fromKey(key) } })}
              >
                <SelectTrigger size="sm" className="w-full text-xs" aria-label="Comparison AI model">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>{models(true)}</SelectContent>
              </Select>
            </Field>
            <Field label="How to send" hint={d.fullScan.useBatch ? "Half price; results can take minutes to hours." : "Full price; results as they come."}>
              <ToggleGroup
                type="single"
                variant="outline"
                size="sm"
                className="w-full"
                value={d.fullScan.useBatch ? "batch" : "direct"}
                onValueChange={(v) => v && set({ fullScan: { ...d.fullScan, useBatch: v === "batch" } })}
                aria-label="How to send"
              >
                <ToggleGroupItem value="batch" className="flex-1 text-xs">
                  Batch
                </ToggleGroupItem>
                <ToggleGroupItem value="direct" className="flex-1 text-xs">
                  Direct
                </ToggleGroupItem>
              </ToggleGroup>
            </Field>
            <NumberField
              label="Budget per comparison ($)"
              value={d.budget}
              placeholder="the plan's estimate + 20%"
              ok={dollars(d.budget, 10_000)}
              hint="The comparison stops before it would spend more, keeps what it found, and can be resumed."
              onChange={(budget) => set({ budget })}
            />
            <NumberField
              label="Start it by itself when it costs up to ($)"
              value={d.autoUsd}
              placeholder="ask me"
              ok={dollars(d.autoUsd, 1000)}
              hint="Empty: the comparison always shows its price and waits for you to press Start."
              onChange={(autoUsd) => set({ autoUsd })}
            />
          </section>
        </>
      )}

      <div className="bg-card sticky bottom-0 flex flex-wrap items-center gap-2 border-t py-3">
        <Button size="sm" disabled={!draft || !valid || save.isPending} onClick={() => save.mutate(d)}>
          {save.isPending ? <Spinner /> : <SaveIcon />}
          Save settings
        </Button>
        {draft && (
          <Button size="sm" variant="ghost" onClick={() => setDraft(null)} disabled={save.isPending}>
            Discard changes
          </Button>
        )}
        {saved && !draft && <span className="text-muted-foreground text-xs">Saved.</span>}
      </div>
      {save.error && (
        <Notice tone="error">
          {/owner/i.test((save.error as Error).message)
            ? "Only the project owner can change these settings."
            : (save.error as Error).message}
        </Notice>
      )}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <p className="text-xs font-medium">{label}</p>
      {children}
      {hint && <p className="text-muted-foreground text-[11px] leading-relaxed">{hint}</p>}
    </div>
  );
}

function NumberField({
  label,
  value,
  placeholder,
  ok,
  hint,
  onChange,
}: {
  label: string;
  value: string;
  placeholder: string;
  ok: boolean;
  hint: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-medium">
      {label}
      <Input
        type="number"
        inputMode="decimal"
        className="text-xs"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={!ok}
      />
      <span className={ok ? "text-muted-foreground text-[11px] font-normal" : "text-destructive text-[11px] font-normal"}>{hint}</span>
    </label>
  );
}
