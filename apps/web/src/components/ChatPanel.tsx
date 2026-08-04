import { useMutation, useQuery } from "@tanstack/react-query";
import { Fragment, useRef, useState } from "react";
import type { ChatSourceDto } from "@cdip/shared";
import { api } from "../api";
import { useAppStore } from "../store";

interface ChatTurn {
  id: number;
  role: "user" | "assistant";
  text: string;
  at: Date;
  sources?: ChatSourceDto[];
  /** Assistant turn that came back as an error — styled as a failure, not an answer. */
  failed?: boolean;
}

const clock = (d: Date) =>
  d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

/**
 * Middle pane (FR-17): project-scoped chat, rendered as a message thread —
 * avatars, send times, and a delivery mark on each question (one tick while
 * the answer is in flight, two once it lands).
 *
 * Answers carry [n] markers whose sources map chunk → document/page/bbox;
 * clicking a marker or the Source line jumps the viewer to that combined page
 * and highlights the cited region (FR-18/FR-19/FR-21). Retrieval can be
 * filtered to one portion (FR-22).
 */
export function ChatPanel({ projectId }: { projectId: string }) {
  const requestJump = useAppStore((s) => s.requestJump);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [portionFilter, setPortionFilter] = useState("");
  const sessionRef = useRef<string | undefined>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(0);

  const portions = useQuery({
    queryKey: ["portions", projectId],
    queryFn: () => api.listPortions(projectId),
  });

  const toBottom = () =>
    queueMicrotask(() =>
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }),
    );

  const ask = useMutation({
    mutationFn: (q: string) =>
      api.ask(projectId, {
        question: q,
        sessionId: sessionRef.current,
        portionId: portionFilter || undefined,
      }),
    onSuccess: (res) => {
      sessionRef.current = res.sessionId;
      setTurns((t) => [
        ...t,
        {
          id: nextId.current++,
          role: "assistant",
          text: res.answer,
          at: new Date(),
          sources: res.sources,
        },
      ]);
      toBottom();
    },
    onError: (err) => {
      setTurns((t) => [
        ...t,
        {
          id: nextId.current++,
          role: "assistant",
          text: (err as Error).message,
          at: new Date(),
          failed: true,
        },
      ]);
      toBottom();
    },
  });

  function submit() {
    const q = question.trim();
    if (!q || ask.isPending) return;
    setTurns((t) => [...t, { id: nextId.current++, role: "user", text: q, at: new Date() }]);
    setQuestion("");
    ask.mutate(q);
    toBottom();
  }

  // The last question is "delivered" only once its answer has arrived.
  const lastUserId = [...turns].reverse().find((t) => t.role === "user")?.id;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-hairline bg-surface px-3 py-2.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-ink-muted">Chat</span>
        <select
          className="ml-auto max-w-44 rounded-md border border-hairline bg-surface px-2 py-1 text-xs text-ink-soft outline-none focus:border-brand-500"
          value={portionFilter}
          onChange={(e) => setPortionFilter(e.target.value)}
          title="Restrict retrieval to one portion"
        >
          <option value="">All portions</option>
          {portions.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-3">
        {turns.length === 0 && (
          <Row avatar={<SparkAvatar />}>
            <div className="rounded-2xl rounded-tl-sm bg-page px-3.5 py-2.5 text-sm leading-relaxed text-ink-soft">
              Ask about the drawings — answers cite their sources, and clicking a source jumps the
              viewer to the exact page.
            </div>
          </Row>
        )}

        {turns.map((turn) =>
          turn.role === "user" ? (
            <div key={turn.id} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-brand-50 px-3.5 py-2.5">
                <p className="text-sm leading-relaxed text-ink">{turn.text}</p>
                <p className="mt-1 flex items-center justify-end gap-1 text-[11px] text-ink-muted">
                  {clock(turn.at)}
                  <DeliveredMark
                    delivered={!(ask.isPending && turn.id === lastUserId)}
                  />
                </p>
              </div>
            </div>
          ) : (
            <Row key={turn.id} avatar={<AssistantAvatar />}>
              <div
                className={`max-w-full rounded-2xl rounded-tl-sm border px-3.5 py-2.5 ${
                  turn.failed
                    ? "border-red-200 bg-red-50"
                    : "border-hairline bg-surface"
                }`}
              >
                {turn.failed ? (
                  <p className="text-sm leading-relaxed text-red-700">
                    Couldn't answer that: {turn.text}
                  </p>
                ) : (
                  <p className="text-sm leading-relaxed text-ink-soft">
                    <AnswerText text={turn.text} sources={turn.sources ?? []} />
                  </p>
                )}

                {turn.sources && turn.sources.length > 0 && (
                  <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                    <span className="text-xs text-ink-muted">Source:</span>
                    {turn.sources.map((s) => (
                      <button
                        key={s.index}
                        className="group inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs font-medium text-ink transition hover:bg-brand-50 hover:text-brand-700"
                        onClick={() => requestJump(s.combinedPageNumber, s.bbox)}
                        title={`Jump to combined page ${s.combinedPageNumber} and highlight the cited region`}
                      >
                        {s.label}
                        <span className="rounded bg-page px-1.5 py-0.5 text-[11px] tabular-nums text-ink-muted group-hover:bg-surface">
                          p.{s.combinedPageNumber}
                        </span>
                      </button>
                    ))}
                  </div>
                )}

                <div className="mt-2 flex items-center gap-1">
                  <span className="text-[11px] text-ink-muted">{clock(turn.at)}</span>
                  {!turn.failed && <Feedback />}
                </div>
              </div>
            </Row>
          ),
        )}

        {ask.isPending && (
          <Row avatar={<AssistantAvatar />}>
            <div className="rounded-2xl rounded-tl-sm border border-hairline bg-surface px-3.5 py-3">
              <TypingDots />
            </div>
          </Row>
        )}
      </div>

      <form
        className="flex items-center gap-2 border-t border-hairline bg-surface p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <input
          className="min-w-0 flex-1 rounded-lg border border-hairline bg-surface px-3.5 py-2.5 text-sm text-ink outline-none placeholder:text-ink-muted focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
          placeholder="Ask about the drawings…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button
          className="shrink-0 rounded-lg bg-brand-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-600 disabled:opacity-50"
          disabled={!question.trim() || ask.isPending}
        >
          Send
        </button>
      </form>
    </div>
  );
}

/** Avatar + bubble, the left-hand (assistant) message shape. */
function Row({ avatar, children }: { avatar: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2">
      {avatar}
      <div className="min-w-0 max-w-[85%]">{children}</div>
    </div>
  );
}

function AssistantAvatar() {
  return (
    <span
      className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-brand-700"
      aria-hidden
    >
      <svg width="16" height="16" viewBox="0 0 34 34" fill="none">
        <path
          d="M9 25a16 16 0 0 1 16-16"
          stroke="white"
          strokeWidth="3"
          strokeLinecap="round"
        />
        <circle cx="9" cy="25" r="2.4" fill="white" />
      </svg>
    </span>
  );
}

function SparkAvatar() {
  return (
    <span
      className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-brand-50 text-brand-700"
      aria-hidden
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <path d="M12 4v5M12 15v5M4 12h5M15 12h5M7 7l3 3M14 14l3 3M17 7l-3 3M10 14l-3 3" />
      </svg>
    </span>
  );
}

/** One tick while the answer is in flight, two once it has arrived. */
function DeliveredMark({ delivered }: { delivered: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={delivered ? "text-brand-500" : "text-ink-muted"}
      aria-label={delivered ? "Answered" : "Sending"}
      role="img"
    >
      {/* Two offset ticks, so "delivered" reads as a double-check rather than
          a check with a stray slash beside it. */}
      <path d={delivered ? "M1 12.5 5 16.5 12.5 8" : "M4 12.5 8 16.5 15.5 8"} />
      {delivered && <path d="M9 12.5 13 16.5 20.5 8" />}
    </svg>
  );
}

/**
 * Answer rating. Local to the session — there is no feedback endpoint yet, so
 * this records nothing server-side; wire it to one before relying on it.
 */
function Feedback() {
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const base =
    "grid h-6 w-6 place-items-center rounded-md text-ink-muted transition hover:bg-page hover:text-ink-soft";
  return (
    <span className="ml-auto flex items-center gap-0.5">
      <button
        className={`${base} ${vote === "up" ? "bg-brand-50 text-brand-700" : ""}`}
        onClick={() => setVote((v) => (v === "up" ? null : "up"))}
        aria-pressed={vote === "up"}
        aria-label="Helpful answer"
        title="Helpful"
      >
        <ThumbIcon />
      </button>
      <button
        className={`${base} ${vote === "down" ? "bg-red-50 text-red-600" : ""}`}
        onClick={() => setVote((v) => (v === "down" ? null : "down"))}
        aria-pressed={vote === "down"}
        aria-label="Unhelpful answer"
        title="Not helpful"
      >
        <ThumbIcon down />
      </button>
    </span>
  );
}

function ThumbIcon({ down = false }: { down?: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinejoin="round"
      style={down ? { transform: "rotate(180deg)" } : undefined}
      aria-hidden
    >
      <path d="M7 21V10l4.5-7A2 2 0 0 1 14 4.6L13 10h5.5a2 2 0 0 1 2 2.4l-1.4 6.6a2 2 0 0 1-2 1.6H7Z" />
      <path d="M7 10H4v11h3" />
    </svg>
  );
}

function TypingDots() {
  return (
    <span className="flex items-center gap-1" aria-label="Thinking">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-muted"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  );
}

/** Renders answer text with [n] markers as clickable citation chips (FR-18). */
function AnswerText({ text, sources }: { text: string; sources: ChatSourceDto[] }) {
  const requestJump = useAppStore((s) => s.requestJump);
  const byIndex = new Map(sources.map((s) => [s.index, s]));
  const parts = text.split(/(\[\d+\])/g);
  return (
    <span className="whitespace-pre-wrap">
      {parts.map((part, i) => {
        const marker = /^\[(\d+)\]$/.exec(part);
        const source = marker ? byIndex.get(Number(marker[1])) : undefined;
        return source ? (
          <button
            key={i}
            className="mx-0.5 rounded bg-brand-50 px-1 text-xs font-semibold text-brand-700 transition hover:bg-brand-100"
            onClick={() => requestJump(source.combinedPageNumber, source.bbox)}
            title={source.label}
          >
            {source.index}
          </button>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        );
      })}
    </span>
  );
}
