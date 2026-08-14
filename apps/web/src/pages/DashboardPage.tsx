import { useQuery } from "@tanstack/react-query";
import {
  FileStackIcon,
  FolderKanbanIcon,
  MessagesSquareIcon,
  PlusIcon,
  SparklesIcon,
  TrendingDownIcon,
  TrendingUpIcon,
} from "lucide-react";
import { useState } from "react";
import type { DashboardDto, TokenTotals, UsageKind } from "@cdip/shared";
import { api } from "@/api";
import { BarList } from "@/charts/BarList";
import { Donut } from "@/charts/Donut";
import { STATUS_COLORS, STATUS_ORDER } from "@/charts/palette";
import { TrendArea } from "@/charts/TrendArea";
import { PageHeader, PageLoading } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAppStore } from "@/store";

/** Ranges the activity chart can ask the API for (GET /dashboard?days=N). */
const RANGES = [
  { days: 7, label: "7 days" },
  { days: 14, label: "14 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
] as const;

/**
 * Account dashboard. One /dashboard call feeds every tile and chart:
 * headline totals, token spend (overall / per stage / per project), the
 * document-status and discipline distributions, and N days of activity.
 */
const KIND_LABEL: Record<UsageKind, string> = {
  chat: "Chat answers",
  summary: "Summaries",
  classification: "Sheet-number reads",
  embedding: "Embeddings",
};

const DISCIPLINE_LABEL = (slug: string) =>
  slug
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");

export const compactNumber = (n: number) =>
  n >= 1_000_000
    ? `${(n / 1_000_000).toFixed(2)}M`
    : n >= 1_000
      ? `${(n / 1_000).toFixed(2)}K`
      : String(n);

export const usd = (n: number) =>
  n >= 0.01 ? `$${n.toFixed(2)}` : n > 0 ? `<$0.01` : "$0.00";

/**
 * Token spend in the newer half of the window against the older half — the
 * only trend the API gives us enough history for, so it is the only tile that
 * gets a delta badge. `null` when there is no earlier spend to compare with.
 */
function halfOverHalf(activity: DashboardDto["activity"]): number | null {
  if (activity.length < 4) return null;
  const half = Math.floor(activity.length / 2);
  const sum = (rows: DashboardDto["activity"]) => rows.reduce((t, a) => t + a.totalTokens, 0);
  const earlier = sum(activity.slice(0, half));
  const later = sum(activity.slice(half));
  if (earlier === 0) return later === 0 ? null : 100;
  return ((later - earlier) / earlier) * 100;
}

export function DashboardPage() {
  const openProject = useAppStore((s) => s.openProject);
  const setView = useAppStore((s) => s.setView);
  const user = useAppStore((s) => s.user);
  const [days, setDays] = useState<number>(14);

  const dashboard = useQuery({
    queryKey: ["dashboard", days],
    queryFn: () => api.dashboard(days),
    // Cheap poll so uploads in another tab show up without a manual refresh.
    refetchInterval: 30_000,
    placeholderData: (previous) => previous, // no flash when the range changes
  });
  const data = dashboard.data;
  const firstName = user?.firstName || user?.name.split(" ")[0] || "there";
  const trend = data ? halfOverHalf(data.activity) : null;
  const rangeLabel = RANGES.find((r) => r.days === days)?.label ?? `${days} days`;

  return (
    <div className="@container/main h-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto flex max-w-[1600px] flex-col gap-4 md:gap-6">
        <PageHeader
          title="Dashboard"
          subtitle={
            <>
              Welcome back, {firstName}! <span aria-hidden>👋</span>
            </>
          }
          action={
            <Button onClick={() => setView("projects")}>
              <PlusIcon />
              Add project
            </Button>
          }
        />

        {dashboard.isError && (
          <Card className="border-destructive/30 bg-destructive/8 py-4">
            <CardContent className="text-destructive text-sm">
              Could not load the dashboard: {(dashboard.error as Error).message}
            </CardContent>
          </Card>
        )}

        {/* First load only — placeholderData keeps the previous range on screen
            while a new one fetches, so this never flashes on a range change. */}
        {dashboard.isLoading && <PageLoading label="Loading dashboard…" />}

        <div className="*:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card dark:*:data-[slot=card]:bg-card grid grid-cols-1 gap-4 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:shadow-xs @xl/main:grid-cols-2 @5xl/main:grid-cols-4">
          <StatCard
            label="Projects"
            value={String(data?.totals.projects ?? "—")}
            icon={<FolderKanbanIcon className="size-4" />}
            headline={data?.totals.projects === 1 ? "One active project" : "Active projects"}
            hint={data ? `${data.recentProjects.length} opened recently` : undefined}
          />
          <StatCard
            label="Drawings processed"
            value={String(data?.totals.documents ?? "—")}
            icon={<FileStackIcon className="size-4" />}
            headline={data ? `${compactNumber(data.totals.pages)} pages extracted` : undefined}
            hint="Across every project you can see"
          />
          <StatCard
            label="Tokens used"
            value={data ? compactNumber(data.tokens.totalTokens) : "—"}
            icon={<SparklesIcon className="size-4" />}
            badge={
              trend === null ? undefined : (
                <Badge variant="outline">
                  {trend >= 0 ? <TrendingUpIcon /> : <TrendingDownIcon />}
                  {trend >= 0 ? "+" : ""}
                  {trend.toFixed(1)}%
                </Badge>
              )
            }
            headline={data ? `${usd(data.tokens.costUsd)} estimated spend` : undefined}
            hint={
              trend === null
                ? `Last ${rangeLabel}`
                : `${trend >= 0 ? "Up" : "Down"} against the previous ${rangeLabel}`
            }
          />
          <StatCard
            label="Chat messages"
            value={data ? compactNumber(data.totals.chatMessages) : "—"}
            icon={<MessagesSquareIcon className="size-4" />}
            headline={data ? `${compactNumber(data.totals.chunks)} indexed chunks` : undefined}
            hint="Every answer is cited back to a chunk"
          />
        </div>

        <Card className="@container/chart">
          <CardHeader>
            <CardTitle>Token usage</CardTitle>
            <CardDescription>
              <span className="@[540px]/chart:block hidden">
                Daily spend across all projects, last {rangeLabel}
              </span>
              <span className="@[540px]/chart:hidden">Last {rangeLabel}</span>
            </CardDescription>
            <CardAction>
              <ToggleGroup
                type="single"
                value={String(days)}
                onValueChange={(value) => value && setDays(Number(value))}
                variant="outline"
                size="sm"
                className="@[540px]/chart:flex hidden"
              >
                {RANGES.map((range) => (
                  <ToggleGroupItem key={range.days} value={String(range.days)} className="px-3">
                    {range.label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
              {/* Narrow cards get the same control as a plain select. */}
              <select
                aria-label="Activity range"
                className="border-input bg-background focus-visible:border-ring focus-visible:ring-ring/50 @[540px]/chart:hidden h-8 rounded-md border px-2 text-sm shadow-xs outline-none focus-visible:ring-[3px]"
                value={days}
                onChange={(event) => setDays(Number(event.target.value))}
              >
                {RANGES.map((range) => (
                  <option key={range.days} value={range.days}>
                    {range.label}
                  </option>
                ))}
              </select>
            </CardAction>
          </CardHeader>
          <CardContent>
            {data ? (
              <TrendArea
                points={data.activity.map((a) => ({
                  date: a.date,
                  value: a.totalTokens,
                  note: usd(a.costUsd),
                }))}
                formatValue={(v) => `${v.toLocaleString()} tokens`}
              />
            ) : (
              <Skeleton className="h-[220px] w-full" />
            )}
          </CardContent>
        </Card>

        <div className="grid gap-4 @3xl/main:grid-cols-2 @6xl/main:grid-cols-3 md:gap-6">
          <Card>
            <CardHeader>
              <CardTitle>Document status</CardTitle>
              <CardDescription>Across every project</CardDescription>
            </CardHeader>
            <CardContent>
              {data ? (
                <Donut
                  centerLabel="documents"
                  centerValue={String(data.totals.documents)}
                  slices={STATUS_ORDER.filter((s) => (data.documentStatus[s] ?? 0) > 0).map((s) => ({
                    key: s,
                    label: s.charAt(0).toUpperCase() + s.slice(1),
                    value: data.documentStatus[s] ?? 0,
                    color: STATUS_COLORS[s]!,
                  }))}
                />
              ) : (
                <Skeleton className="h-[176px] w-full" />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Pages by discipline</CardTitle>
              <CardDescription>From the sheet number on each page</CardDescription>
            </CardHeader>
            <CardContent>
              {data ? (
                <BarList
                  rows={data.disciplines
                    .slice(0, 8)
                    .map((d) => ({ label: DISCIPLINE_LABEL(d.discipline), value: d.pages }))}
                  unit="pages"
                  emptyLabel="No pages classified yet"
                />
              ) : (
                <Skeleton className="h-[176px] w-full" />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Spend by stage</CardTitle>
              <CardDescription>Where the tokens go</CardDescription>
            </CardHeader>
            <CardContent>
              {data ? (
                <BarList
                  rows={(Object.entries(data.tokensByKind) as [UsageKind, TokenTotals][])
                    .map(([kind, t]) => ({ label: KIND_LABEL[kind] ?? kind, value: t.totalTokens }))
                    .sort((a, b) => b.value - a.value)}
                  unit="tokens"
                  emptyLabel="No model calls recorded yet"
                />
              ) : (
                <Skeleton className="h-[176px] w-full" />
              )}
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-4 @4xl/main:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] md:gap-6">
          {data && data.projects.length > 0 ? (
            <ProjectUsageTable projects={data.projects} />
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>Per-project usage</CardTitle>
                <CardDescription>Documents, pages and token spend</CardDescription>
              </CardHeader>
              <CardContent className="text-muted-foreground py-8 text-center text-sm">
                No projects yet — create one to start uploading drawings.
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Recent projects</CardTitle>
              <CardDescription>Most recently created</CardDescription>
            </CardHeader>
            <CardContent>
              {data && data.recentProjects.length > 0 ? (
                <ul className="divide-y">
                  {data.recentProjects.map((p) => (
                    <li key={p.id}>
                      <button
                        className="hover:bg-muted/60 -mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-md px-2 py-2.5 text-left transition-colors"
                        onClick={() => openProject(p.id)}
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium">{p.name}</span>
                          <span className="text-muted-foreground block text-xs">
                            {p.documents} document{p.documents === 1 ? "" : "s"} ·{" "}
                            {compactNumber(p.pages)} pages
                          </span>
                        </span>
                        <StatusPill status={p.status} />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted-foreground py-8 text-center text-sm">No projects yet</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

/**
 * KPI tile. `badge` is reserved for a real measured delta — a tile with no
 * history to compare against carries a plain headline instead of an invented
 * percentage.
 */
function StatCard({
  label,
  value,
  icon,
  badge,
  headline,
  hint,
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  badge?: React.ReactNode;
  headline?: string;
  hint?: string;
}) {
  return (
    <Card className="@container/card gap-4 py-5">
      <CardHeader>
        <CardDescription className="flex items-center gap-2">
          {icon}
          {label}
        </CardDescription>
        <CardTitle className="text-2xl font-semibold tabular-nums @[220px]/card:text-3xl">
          {value}
        </CardTitle>
        {badge && <CardAction>{badge}</CardAction>}
      </CardHeader>
      <CardFooter className="flex-col items-start gap-1 text-sm">
        {headline && <div className="line-clamp-1 font-medium">{headline}</div>}
        {hint && <div className="text-muted-foreground line-clamp-1 text-xs">{hint}</div>}
      </CardFooter>
    </Card>
  );
}

/** The per-project breakdown — the table view that relieves the chart colors. */
function ProjectUsageTable({ projects }: { projects: DashboardDto["projects"] }) {
  const openProject = useAppStore((s) => s.openProject);
  const rows = [...projects].sort((a, b) => b.tokens.totalTokens - a.tokens.totalTokens);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Per-project usage</CardTitle>
        <CardDescription>Documents, pages and token spend</CardDescription>
      </CardHeader>
      <CardContent className="px-2">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-4">Project</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Documents</TableHead>
              <TableHead className="text-right">Pages</TableHead>
              <TableHead className="text-right">Tokens</TableHead>
              <TableHead className="pr-4 text-right">Est. cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((p) => (
              <TableRow key={p.id}>
                <TableCell className="pl-4">
                  <button className="font-medium hover:underline" onClick={() => openProject(p.id)}>
                    {p.name}
                  </button>
                </TableCell>
                <TableCell>
                  <StatusPill status={p.status} />
                </TableCell>
                <TableCell className="text-right tabular-nums">{p.documents}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {p.pages.toLocaleString()}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {p.tokens.totalTokens.toLocaleString()}
                </TableCell>
                <TableCell className="pr-4 text-right tabular-nums">
                  {usd(p.tokens.costUsd)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

export function StatusPill({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? STATUS_COLORS.empty!;
  const label = status === "empty" ? "No documents" : status.charAt(0).toUpperCase() + status.slice(1);
  return (
    <Badge variant="outline" className="gap-1.5 font-normal">
      <span className="size-1.5 rounded-full" style={{ background: color }} aria-hidden />
      {label}
    </Badge>
  );
}
