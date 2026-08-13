import type { SummaryEstimateDto } from "@cdip/shared";
import { Modal, Spinner } from "@/components/shared";
import { Button } from "@/components/ui/button";

/**
 * Confirmation before a summary run. Summaries are the most expensive thing a
 * user can trigger by hand, and the cost is not obvious from the button — a
 * dense discipline is several dollars, an already-summarized one is cents. So
 * the numbers come first and the spend is a deliberate second click.
 *
 * Call counts are exact (they mirror the worker's tier structure); the output
 * length is calibrated from observed runs, so the dialog says "about".
 */

const money = (usd: number) =>
  usd < 0.01 ? "<$0.01" : `$${usd.toFixed(usd < 1 ? 3 : 2)}`;

const compact = (tokens: number) =>
  tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}K` : String(tokens);

export function SummaryConfirm({
  title,
  estimate,
  isLoading,
  error,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  estimate: SummaryEstimateDto | undefined;
  isLoading: boolean;
  error: unknown;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const nothingToDo = estimate?.totalCalls === 0;

  return (
    <Modal
      title={title}
      onClose={() => !busy && onCancel()}
      footer={
        <>
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={onConfirm} disabled={busy || isLoading || nothingToDo}>
            {busy && <Spinner />}
            {busy ? "Starting…" : "Generate summary"}
          </Button>
        </>
      }
    >
      {isLoading && (
        <p className="mt-3 text-sm text-muted-foreground">Working out the cost…</p>
      )}

      {!!error && (
        <p className="mt-3 text-sm text-destructive">
          Could not estimate the cost: {(error as Error).message}
        </p>
      )}

      {estimate && !isLoading && (
        <>
          {nothingToDo ? (
            <p className="mt-3 text-sm text-muted-foreground">
              There is nothing to summarize here — this discipline has no pages
              with extracted text yet.
            </p>
          ) : (
            <>
              <div className="mt-3 rounded-md border border-border">
                <div className="flex items-baseline justify-between border-b border-border px-3 py-2.5">
                  <span className="text-sm text-muted-foreground">Estimated cost</span>
                  <span className="text-lg font-semibold tabular-nums text-foreground">
                    about {money(estimate.costUsd)}
                  </span>
                </div>
                <dl className="divide-y divide-border text-xs">
                  <Row
                    label="Model calls"
                    value={`${estimate.totalCalls}`}
                    detail={[
                      estimate.pageCalls > 0 && `${estimate.pageCalls} page`,
                      estimate.sectionCalls > 0 &&
                        `${estimate.sectionCalls} section`,
                      estimate.portionCalls > 0 &&
                        `${estimate.portionCalls} rollup`,
                    ]
                      .filter(Boolean)
                      .join(" + ")}
                  />
                  <Row
                    label="Tokens"
                    value={`~${compact(estimate.inputTokens + estimate.outputTokens)}`}
                    detail={`${compact(estimate.inputTokens)} in + ${compact(
                      estimate.outputTokens,
                    )} out`}
                  />
                  {estimate.pages !== undefined && (
                    <Row
                      label="Sheets"
                      value={`${estimate.pages}`}
                      detail={
                        estimate.reusedPageSummaries
                          ? `${estimate.pagesToSummarize} to summarize, ${estimate.reusedPageSummaries} reused free`
                          : undefined
                      }
                    />
                  )}
                  {estimate.portionsUsed !== undefined && (
                    <Row
                      label="Disciplines combined"
                      value={`${estimate.portionsUsed}`}
                    />
                  )}
                  <Row label="Model" value={estimate.model} />
                </dl>
              </div>
              <p className="mt-2 text-[11px] leading-snug text-muted-foreground">
                The call count is exact; the length of each answer is estimated
                from previous runs, so the real cost will differ somewhat.
              </p>
            </>
          )}
        </>
      )}
    </Modal>
  );
}

function Row({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string | false;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 px-3 py-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right">
        <span className="font-medium tabular-nums text-foreground">{value}</span>
        {detail && <span className="ml-2 text-muted-foreground">{detail}</span>}
      </dd>
    </div>
  );
}
