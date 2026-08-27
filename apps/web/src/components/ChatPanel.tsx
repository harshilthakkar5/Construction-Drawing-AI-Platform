import { useMutation, useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useRef, useState } from "react";
import type { ChatHistoryWindow, ChatMessageDto, ChatSourceDto } from "@cdip/shared";
import {
  HistoryIcon,
  Maximize2Icon,
  Minimize2Icon,
  PlusIcon,
  SendHorizonalIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react";
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

/** Same day → time only; this year → day + month; older → include the year. */
function whenLabel(iso: string): string {
  const at = new Date(iso);
  const now = new Date();
  if (at.toDateString() === now.toDateString()) return clock(at);
  return at.toLocaleDateString([], {
    day: "numeric",
    month: "short",
    year: at.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
}

const HISTORY_WINDOW_LABELS: Record<ChatHistoryWindow, string> = {
  "1h": "Last hour",
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "3m": "Last 3 months",
  all: "All time",
  custom: "Custom range…",
};

/**
 * Which conversation a project was last on. The thread lives on the server
 * (FR-23) but WHICH thread you were reading is a per-browser preference, so it
 * belongs here rather than in a table — and it is what makes leaving a project
 * and coming back resume where you were instead of on a blank panel.
 */
const sessionKey = (projectId: string) => `cdip-chat-session:${projectId}`;

function rememberSession(projectId: string, sessionId: string | undefined) {
  try {
    if (sessionId) localStorage.setItem(sessionKey(projectId), sessionId);
    else localStorage.removeItem(sessionKey(projectId));
  } catch {
    /* private mode, or site data blocked — the chat still works, it just
       starts fresh next time. */
  }
}

function recallSession(projectId: string): string | undefined {
  try {
    return localStorage.getItem(sessionKey(projectId)) ?? undefined;
  } catch {
    return undefined;
  }
}

/** A persisted message row → the shape the thread renders. */
function toTurn(message: ChatMessageDto, id: number): ChatTurn | null {
  const content = (message.content ?? {}) as Record<string, unknown>;
  const text =
    message.role === "user"
      ? String(content.question ?? "")
      : String(content.displayedAnswer ?? content.answer ?? "");
  if (!text) return null;
  return {
    id,
    role: message.role,
    text,
    at: new Date(message.createdAt),
    sources: message.sources ?? undefined,
  };
}

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
export function ChatPanel({
  projectId,
  expanded = false,
  onToggleExpand,
}: {
  projectId: string;
  /** Full-page mode: the chat is the only pane, so it gets more room. */
  expanded?: boolean;
  onToggleExpand?: () => void;
}) {
  const requestJump = useAppStore((s) => s.requestJump);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [portionFilter, setPortionFilter] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const sessionRef = useRef<string | undefined>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(0);

  const portions = useQuery({
    queryKey: ["portions", projectId],
    queryFn: () => api.listPortions(projectId),
  });

  /** Load a stored conversation into the thread (history pick, or resume). */
  const openSession = useMutation({
    mutationFn: (sessionId: string) => api.chatMessages(projectId, sessionId),
    onSuccess: (messages, sessionId) => {
      const restored = messages
        .map((m) => toTurn(m, nextId.current++))
        .filter((t): t is ChatTurn => t !== null);
      setTurns(restored);
      sessionRef.current = sessionId;
      rememberSession(projectId, sessionId);
      setHistoryOpen(false);
      toBottom();
    },
    onError: () => {
      // The stored id is gone (project re-created, history purged). Start clean
      // rather than showing an error for something the user never asked for.
      rememberSession(projectId, undefined);
      sessionRef.current = undefined;
    },
  });

  // Switching projects unmounts this panel, so the thread is rebuilt from the
  // server on the way back rather than kept in memory for every project.
  useEffect(() => {
    setTurns([]);
    setQuestion("");
    sessionRef.current = undefined;
    const previous = recallSession(projectId);
    if (previous) openSession.mutate(previous);
    // openSession is stable enough for this purpose; re-running on identity
    // changes would refetch the thread on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  function startNewChat() {
    setTurns([]);
    sessionRef.current = undefined;
    rememberSession(projectId, undefined);
    setHistoryOpen(false);
  }

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
      rememberSession(projectId, res.sessionId);
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

        <IconAction
          label="New chat"
          onClick={startNewChat}
          disabled={turns.length === 0 && !sessionRef.current}
        >
          <PlusIcon className="size-4" />
        </IconAction>
        <IconAction
          label="Chat history"
          onClick={() => setHistoryOpen((open) => !open)}
          active={historyOpen}
        >
          <HistoryIcon className="size-4" />
        </IconAction>
        {onToggleExpand && (
          <IconAction
            label={expanded ? "Exit full page" : "Full page chat"}
            onClick={onToggleExpand}
          >
            {expanded ? <Minimize2Icon className="size-4" /> : <Maximize2Icon className="size-4" />}
          </IconAction>
        )}
      </div>

      {historyOpen && (
        <HistoryList
          projectId={projectId}
          activeSessionId={sessionRef.current}
          loading={openSession.isPending}
          onPick={(sessionId) => openSession.mutate(sessionId)}
          onClose={() => setHistoryOpen(false)}
        />
      )}

      <div
        ref={scrollRef}
        className={cn(
          "flex-1 space-y-4 overflow-y-auto p-3",
          // Full page is much wider than the middle pane; an unbounded line
          // length is hard to read, so the thread keeps a column.
          expanded && "mx-auto w-full max-w-3xl px-6 py-6",
        )}
      >
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
        className={cn(
          "flex items-center gap-2 border-t border-border bg-card p-3",
          expanded && "justify-center",
        )}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Input
          className={cn("min-w-0 flex-1", expanded && "max-w-2xl")}
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

/** Small square header button — the chat toolbar's shape. */
function IconAction({
  label,
  onClick,
  children,
  active,
  disabled,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  active?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      aria-pressed={active}
      className={cn(
        "text-muted-foreground hover:bg-muted hover:text-foreground grid h-8 w-8 shrink-0 place-items-center rounded-md transition",
        active && "bg-accent text-accent-foreground",
        disabled && "pointer-events-none opacity-40",
      )}
    >
      {children}
    </button>
  );
}

/**
 * Past conversations for this project, filtered by when they were last used.
 *
 * The window filters on LAST activity rather than when the session started, so
 * a thread opened last week and continued this morning is where a reader
 * expects it: under "last 24 hours".
 */
function HistoryList({
  projectId,
  activeSessionId,
  loading,
  onPick,
  onClose,
}: {
  projectId: string;
  activeSessionId?: string;
  loading: boolean;
  onPick: (sessionId: string) => void;
  onClose: () => void;
}) {
  const [window, setWindow] = useState<ChatHistoryWindow>("30d");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const custom = window === "custom";
  const sessions = useQuery({
    queryKey: ["chat-sessions", projectId, window, from, to],
    queryFn: () =>
      api.chatSessions(projectId, {
        window,
        from: custom && from ? new Date(from).toISOString() : undefined,
        // A date input means the whole of that day, so the range runs to its
        // last moment — otherwise "to: today" excludes everything said today.
        to: custom && to ? new Date(`${to}T23:59:59.999`).toISOString() : undefined,
      }),
  });

  return (
    <div className="bg-muted/40 border-b">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <span className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
          History
        </span>
        <Select value={window} onValueChange={(v) => setWindow(v as ChatHistoryWindow)}>
          <SelectTrigger size="sm" className="max-w-40 text-xs" aria-label="History range">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(HISTORY_WINDOW_LABELS).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {custom && (
          <span className="flex items-center gap-1.5">
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              aria-label="From date"
              className="border-input bg-background h-8 rounded-md border px-2 text-xs"
            />
            <span className="text-muted-foreground text-xs">to</span>
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              aria-label="To date"
              className="border-input bg-background h-8 rounded-md border px-2 text-xs"
            />
          </span>
        )}
        <Button variant="ghost" size="sm" className="ml-auto text-xs" onClick={onClose}>
          Close
        </Button>
      </div>

      <div className="max-h-56 overflow-y-auto px-2 pb-2">
        {sessions.isPending && (
          <p className="text-muted-foreground px-2 py-3 text-xs">Loading conversations…</p>
        )}
        {sessions.isError && (
          <p className="text-destructive px-2 py-3 text-xs">
            Could not load history: {(sessions.error as Error).message}
          </p>
        )}
        {sessions.data?.length === 0 && (
          <p className="text-muted-foreground px-2 py-3 text-xs">
            No conversations in this range.
          </p>
        )}
        {sessions.data?.map((session) => (
          <button
            key={session.id}
            type="button"
            onClick={() => onPick(session.id)}
            disabled={loading}
            className={cn(
              "hover:bg-accent hover:text-accent-foreground flex w-full items-center gap-2 rounded-md px-2 py-2 text-left transition",
              session.id === activeSessionId && "bg-accent text-accent-foreground",
            )}
          >
            <span className="min-w-0 flex-1 truncate text-xs">
              {session.preview || "Untitled conversation"}
            </span>
            <span className="text-muted-foreground shrink-0 text-[11px] tabular-nums">
              {session.messageCount / 2 >= 1 ? Math.round(session.messageCount / 2) : 1} Q ·{" "}
              {whenLabel(session.lastMessageAt)}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
