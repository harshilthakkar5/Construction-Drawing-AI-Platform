import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { DashboardDto, TokenTotals, UsageKind } from "@cdip/shared";
import { api } from "../api";
import { BarList } from "../charts/BarList";
import { Donut } from "../charts/Donut";
import { STATUS_COLORS, STATUS_ORDER } from "../charts/palette";
import { TrendArea } from "../charts/TrendArea";
import { ActionButton, Card, PageHeader, PageLoading, StatTile } from "../components/ui";
import { useAppStore } from "../store";

/** Ranges the activity chart can ask the API for (GET /dashboard?days=N). */
const RANGES = [
  { days: 7, label: "Last 7 days" },
  { days: 14, label: "Last 14 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
] as const;

/**
 * Account dashboard. One /dashboard call feeds every tile and chart:
 * headline totals, token spend (overall / per stage / per project), the
 * document-status and discipline distributions, and 14 days of activity.
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

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8">
      <PageHeader
        title="Dashboard"
        subtitle={
          <>
            Welcome back, {firstName}! <span aria-hidden>👋</span>
          </>
        }
        action={<ActionButton icon={<PlusIcon />} onClick={() => setView("projects")}>Add Project</ActionButton>}
      />

      {dashboard.isError && (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          Could not load the dashboard: {(dashboard.error as Error).message}
        </p>
      )}

      {/* First load only — placeholderData keeps the previous range on screen
          while a new one fetches, so this never flashes on a range change. */}
      {dashboard.isLoading && <PageLoading label="Loading dashboard…" />}

      <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Projects"
          value={String(data?.totals.projects ?? "—")}
          hint={data ? `${data.totals.projects === 1 ? "Active project" : "Active projects"}` : undefined}
          icon={<BriefcaseIcon />}
          tint="violet"
          art="buildings"
        />
        <StatTile
          label="Drawings processed"
          value={`${data?.totals.documents ?? "—"}`}
          hint={data ? `${compactNumber(data.totals.pages)} pages` : undefined}
          icon={<PagesIcon />}
          tint="blue"
          art="blueprint"
        />
        <StatTile
          label="Tokens used"
          value={data ? compactNumber(data.tokens.totalTokens) : "—"}
          hint={data ? `${usd(data.tokens.costUsd)} estimated` : undefined}
          icon={<SparkIcon />}
          tint="green"
          art="coins"
        />
        <StatTile
          label="Chat messages"
          value={data ? compactNumber(data.totals.chatMessages) : "—"}
          hint={data ? `${compactNumber(data.totals.chunks)} indexed chunks` : undefined}
          icon={<ChatIcon />}
          tint="indigo"
          art="chat"
        />
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-3">
        <Card
          className="xl:col-span-2"
          title="Token usage"
          subtitle={`${RANGES.find((r) => r.days === days)?.label ?? ""}, all projects`}
          art="trend"
          action={
            <select
              className="rounded-lg border border-hairline bg-surface px-2.5 py-1.5 text-xs font-medium text-ink-soft outline-none focus:border-accent-500"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              aria-label="Activity range"
            >
              {RANGES.map((r) => (
                <option key={r.days} value={r.days}>
                  {r.label}
                </option>
              ))}
            </select>
          }
        >
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
            <Skeleton height={180} />
          )}
        </Card>

        <Card title="Document status" subtitle="Across every project" art="document">
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
            <Skeleton height={200} />
          )}
        </Card>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-3">
        <Card title="Pages by discipline" subtitle="From the sheet number on each page" art="columns">
          {data ? (
            <BarList
              rows={data.disciplines
                .slice(0, 8)
                .map((d) => ({ label: DISCIPLINE_LABEL(d.discipline), value: d.pages }))}
              unit="pages"
              emptyLabel="No pages classified yet"
            />
          ) : (
            <Skeleton height={200} />
          )}
        </Card>

        <Card title="Spend by stage" subtitle="Where the tokens go" art="funnel">
          {data ? (
            <BarList
              rows={(Object.entries(data.tokensByKind) as [UsageKind, TokenTotals][])
                .map(([kind, t]) => ({ label: KIND_LABEL[kind] ?? kind, value: t.totalTokens }))
                .sort((a, b) => b.value - a.value)}
              unit="tokens"
              emptyLabel="No model calls recorded yet"
            />
          ) : (
            <Skeleton height={200} />
          )}
        </Card>

        <Card title="Recent projects" subtitle="Most recently created" art="folder">
          {data && data.recentProjects.length > 0 ? (
            <ul className="divide-y divide-hairline">
              {data.recentProjects.map((p) => (
                <li key={p.id}>
                  <button
                    className="flex w-full items-center gap-3 py-2.5 text-left hover:opacity-80"
                    onClick={() => openProject(p.id)}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-ink">{p.name}</span>
                      <span className="block text-xs text-ink-muted">
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
            <p className="py-8 text-center text-sm text-ink-muted">No projects yet</p>
          )}
        </Card>
      </div>

      {data && data.projects.length > 0 && <ProjectUsageTable projects={data.projects} />}
    </div>
  );
}

/** The per-project breakdown — the table view that relieves the chart colors. */
function ProjectUsageTable({ projects }: { projects: DashboardDto["projects"] }) {
  const openProject = useAppStore((s) => s.openProject);
  const rows = [...projects].sort((a, b) => b.tokens.totalTokens - a.tokens.totalTokens);
  return (
    <Card className="mt-5" title="Per-project usage" subtitle="Documents, pages and token spend" art="pie">
      <div className="-mx-4 overflow-x-auto px-4">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-b border-hairline text-left text-xs uppercase tracking-wide text-ink-muted">
              <th className="py-2 font-medium">Project</th>
              <th className="py-2 font-medium">Status</th>
              <th className="py-2 text-right font-medium">Documents</th>
              <th className="py-2 text-right font-medium">Pages</th>
              <th className="py-2 text-right font-medium">Tokens</th>
              <th className="py-2 text-right font-medium">Est. cost</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {rows.map((p) => (
              <tr key={p.id} className="hover:bg-page">
                <td className="py-2.5">
                  <button
                    className="font-medium text-ink hover:underline"
                    onClick={() => openProject(p.id)}
                  >
                    {p.name}
                  </button>
                </td>
                <td className="py-2.5">
                  <StatusPill status={p.status} />
                </td>
                <td className="py-2.5 text-right tabular-nums text-ink-soft">{p.documents}</td>
                <td className="py-2.5 text-right tabular-nums text-ink-soft">
                  {p.pages.toLocaleString()}
                </td>
                <td className="py-2.5 text-right tabular-nums text-ink-soft">
                  {p.tokens.totalTokens.toLocaleString()}
                </td>
                <td className="py-2.5 text-right tabular-nums text-ink-soft">
                  {usd(p.tokens.costUsd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function StatusPill({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? STATUS_COLORS.empty!;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-page px-2 py-0.5 text-xs font-medium text-ink-soft">
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} aria-hidden />
      {status === "empty" ? "no documents" : status}
    </span>
  );
}

function Skeleton({ height }: { height: number }) {
  return <div className="animate-pulse rounded-md bg-page" style={{ height }} />;
}

const ic = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};
const BriefcaseIcon = () => (
  <svg {...ic}>
    <rect x="3" y="7" width="18" height="13" rx="2" />
    <path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
  </svg>
);
const PagesIcon = () => (
  <svg {...ic}>
    <path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
    <path d="M14 3v5h5M9 13h6M9 17h6" />
  </svg>
);
const SparkIcon = () => (
  <svg {...ic}>
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />
  </svg>
);
const PlusIcon = () => (
  <svg {...ic} width={16} height={16}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);
const ChatIcon = () => (
  <svg {...ic}>
    <path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-6a8 8 0 0 1 8-8h2a8 8 0 0 1 8 3Z" />
  </svg>
);
