import { useMutation, useQuery } from "@tanstack/react-query";
import { Fragment, useRef, useState } from "react";
import type { ChatSourceDto } from "@cdip/shared";
import { SendHorizonalIcon, ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import { api } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/store";

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
      <div className="bg-card flex items-center gap-2 border-b px-3 py-2.5">
        <span className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
          Chat
        </span>
        <Select
          value={portionFilter || "all"}
          onValueChange={(value) => setPortionFilter(value === "all" ? "" : value)}
        >
          <SelectTrigger
            size="sm"
            className="ml-auto max-w-44 text-xs"
            title="Restrict retrieval to one portion"
            aria-label="Portion filter"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All portions</SelectItem>
            {portions.data?.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-3">
        {turns.length === 0 && (
          <Row avatar={<SparkAvatar />}>
            <div className="rounded-2xl rounded-tl-sm bg-muted px-3.5 py-2.5 text-sm leading-relaxed text-muted-foreground">
              Ask about the drawings — answers cite their sources, and clicking a source jumps the
              viewer to the exact page.
            </div>
          </Row>
        )}

        {turns.map((turn) =>
          turn.role === "user" ? (
            <div key={turn.id} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-accent px-3.5 py-2.5">
                <p className="text-sm leading-relaxed text-foreground">{turn.text}</p>
                <p className="mt-1 flex items-center justify-end gap-1 text-[11px] text-muted-foreground">
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
                    ? "border-destructive/30 bg-destructive/10"
                    : "border-border bg-card"
                }`}
              >
                {turn.failed ? (
                  <p className="text-sm leading-relaxed text-destructive">
                    Couldn't answer that: {turn.text}
                  </p>
                ) : (
                  <p className="text-sm leading-relaxed">
                    <AnswerText text={turn.text} sources={turn.sources ?? []} />
                  </p>
                )}

                {turn.sources && turn.sources.length > 0 && (
                  <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                    <span className="text-xs text-muted-foreground">Source:</span>
                    {turn.sources.map((s) => (
                      <button
                        key={s.index}
                        className="group inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-xs font-medium text-foreground transition hover:bg-accent hover:text-accent-foreground"
                        onClick={() => requestJump(s.combinedPageNumber, s.bbox)}
                        title={`Jump to combined page ${s.combinedPageNumber} and highlight the cited region`}
                      >
                        {s.label}
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] tabular-nums text-muted-foreground group-hover:bg-card">
                          p.{s.combinedPageNumber}
                        </span>
                      </button>
                    ))}
                  </div>
                )}

                <div className="mt-2 flex items-center gap-1">
                  <span className="text-[11px] text-muted-foreground">{clock(turn.at)}</span>
                  {!turn.failed && <Feedback />}
                </div>
              </div>
            </Row>
          ),
        )}

        {ask.isPending && (
          <Row avatar={<AssistantAvatar />}>
            <div className="rounded-2xl rounded-tl-sm border border-border bg-card px-3.5 py-3">
              <TypingDots />
            </div>
          </Row>
        )}
      </div>

      <form
        className="flex items-center gap-2 border-t border-border bg-card p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Input
          className="min-w-0 flex-1"
          placeholder="Ask about the drawings…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          aria-label="Question"
        />
        <Button type="submit" size="icon" disabled={!question.trim() || ask.isPending}>
          <SendHorizonalIcon />
          <span className="sr-only">Send</span>
        </Button>
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
      className="bg-primary text-primary-foreground mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg"
      aria-hidden
    >
      <svg width="16" height="16" viewBox="0 0 34 34" fill="none">
        <path
          d="M9 25a16 16 0 0 1 16-16"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
        />
        <circle cx="9" cy="25" r="2.4" fill="currentColor" />
      </svg>
    </span>
  );
}

function SparkAvatar() {
  return (
    <span
      className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground"
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
      className={delivered ? "text-primary" : "text-muted-foreground"}
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
    "text-muted-foreground hover:bg-muted hover:text-foreground grid h-6 w-6 place-items-center rounded-md transition";
  return (
    <span className="ml-auto flex items-center gap-0.5">
      <button
        className={cn(base, vote === "up" && "bg-accent text-accent-foreground")}
        onClick={() => setVote((v) => (v === "up" ? null : "up"))}
        aria-pressed={vote === "up"}
        aria-label="Helpful answer"
        title="Helpful"
      >
        <ThumbsUpIcon className="size-3.5" />
      </button>
      <button
        className={cn(base, vote === "down" && "bg-destructive/10 text-destructive")}
        onClick={() => setVote((v) => (v === "down" ? null : "down"))}
        aria-pressed={vote === "down"}
        aria-label="Unhelpful answer"
        title="Not helpful"
      >
        <ThumbsDownIcon className="size-3.5" />
      </button>
    </span>
  );
}

function TypingDots() {
  return (
    <span className="flex items-center gap-1" aria-label="Thinking">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground"
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
            className="mx-0.5 rounded bg-accent text-accent-foreground px-1 text-xs font-semibold transition hover:bg-accent/70"
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
