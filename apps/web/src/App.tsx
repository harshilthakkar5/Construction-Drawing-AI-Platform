import { useQuery } from "@tanstack/react-query";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:4000";

interface HealthResponse {
  status: "ok" | "degraded";
  checks: Record<string, "ok" | "error">;
}

function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async (): Promise<HealthResponse> => {
      const res = await fetch(`${API_URL}/health`);
      return res.json();
    },
    refetchInterval: 10_000,
  });
}

/**
 * FR-17 three-pane layout: sidebar (summary + portions) | chat | PDF viewer.
 * Panes are placeholders — scaffold only.
 */
export function App() {
  const health = useHealth();

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-gray-200 px-4 py-2">
        <h1 className="text-lg font-semibold">Construction Drawing AI Platform</h1>
        <span className="text-sm text-gray-500">
          API:{" "}
          {health.isLoading ? "checking…" : (health.data?.status ?? "unreachable")}
        </span>
      </header>
      <main className="flex flex-1 overflow-hidden">
        <aside className="w-64 border-r border-gray-200 p-4">
          <h2 className="font-medium">Summary & portions</h2>
          <p className="mt-2 text-sm text-gray-500">Portion list will render here.</p>
        </aside>
        <section className="flex-1 border-r border-gray-200 p-4">
          <h2 className="font-medium">Chat</h2>
          <p className="mt-2 text-sm text-gray-500">
            Project-scoped chat with clickable sources will render here.
          </p>
        </section>
        <section className="flex-1 p-4">
          <h2 className="font-medium">Combined PDF viewer</h2>
          <p className="mt-2 text-sm text-gray-500">
            Lazy-loading viewer with jump + highlight will render here.
          </p>
        </section>
      </main>
    </div>
  );
}
