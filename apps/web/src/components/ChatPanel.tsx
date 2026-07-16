import { useMutation, useQuery } from "@tanstack/react-query";
import { Fragment, useRef, useState } from "react";
import type { ChatSourceDto } from "@cdip/shared";
import { api } from "../api";
import { useAppStore } from "../store";

interface ChatTurn {
  role: "user" | "assistant";
  text: string;
  sources?: ChatSourceDto[];
}

/**
 * Middle pane (FR-17): project-scoped chat. Answers carry [n] markers whose
 * sources map chunk → document/page/bbox; clicking a marker or source chip
 * jumps the viewer to that combined page (FR-18/FR-21). Retrieval can be
 * filtered to one portion (FR-22).
 */
export function ChatPanel({ projectId }: { projectId: string }) {
  const requestJump = useAppStore((s) => s.requestJump);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [portionFilter, setPortionFilter] = useState("");
  const sessionRef = useRef<string | undefined>(undefined);
  const scrollRef = useRef<HTMLDivElement>(null);

  const portions = useQuery({
    queryKey: ["portions", projectId],
    queryFn: () => api.listPortions(projectId),
  });

  const ask = useMutation({
    mutationFn: (q: string) =>
      api.ask(projectId, {
        question: q,
        sessionId: sessionRef.current,
        portionId: portionFilter || undefined,
      }),
    onSuccess: (res) => {
      sessionRef.current = res.sessionId;
      setTurns((t) => [...t, { role: "assistant", text: res.answer, sources: res.sources }]);
      queueMicrotask(() =>
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }),
      );
    },
    onError: (err) => {
      setTurns((t) => [...t, { role: "assistant", text: `Error: ${(err as Error).message}` }]);
    },
  });

  function submit() {
    const q = question.trim();
    if (!q || ask.isPending) return;
    setTurns((t) => [...t, { role: "user", text: q }]);
    setQuestion("");
    ask.mutate(q);
    queueMicrotask(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }));
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-gray-200 px-3 py-2">
        <span className="text-sm font-medium">Chat</span>
        <select
          className="ml-auto max-w-44 rounded border border-gray-300 px-2 py-1 text-xs"
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

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-3">
        {turns.length === 0 && (
          <p className="text-sm text-gray-500">
            Ask about the drawings — answers cite their sources, and clicking a source jumps the
            viewer to the exact page.
          </p>
        )}
        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <div key={i} className="ml-8 rounded-lg bg-blue-600 p-2 text-sm text-white">
              {turn.text}
            </div>
          ) : (
            <div key={i} className="mr-8 rounded-lg border border-gray-200 bg-white p-2 text-sm">
              <AnswerText text={turn.text} sources={turn.sources ?? []} />
              {turn.sources && turn.sources.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1 border-t border-gray-100 pt-2">
                  {turn.sources.map((s) => (
                    <button
                      key={s.index}
                      className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-blue-700 hover:bg-blue-50"
                      onClick={() => requestJump(s.combinedPageNumber)}
                      title={`Jump to combined page ${s.combinedPageNumber}`}
                    >
                      [{s.index}] {s.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ),
        )}
        {ask.isPending && <div className="mr-8 text-sm text-gray-400">Thinking…</div>}
      </div>

      <form
        className="flex gap-2 border-t border-gray-200 p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <input
          className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm"
          placeholder="Ask about the drawings…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button
          className="rounded bg-blue-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          disabled={!question.trim() || ask.isPending}
        >
          Send
        </button>
      </form>
    </div>
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
            className="mx-0.5 rounded bg-blue-100 px-1 text-xs font-medium text-blue-700 hover:bg-blue-200"
            onClick={() => requestJump(source.combinedPageNumber)}
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
