import { useQuery } from "@tanstack/react-query";
import { API_URL } from "./api";
import { ProjectList } from "./components/ProjectList";
import { ProjectView } from "./components/ProjectView";
import { useAppStore } from "./store";

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
    refetchInterval: 15_000,
  });
}

export function App() {
  const health = useHealth();
  const selectedProjectId = useAppStore((s) => s.selectedProjectId);

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-gray-200 px-4 py-2">
        <h1 className="text-lg font-semibold">Construction Drawing AI Platform</h1>
        <span className="text-sm text-gray-500">
          API: {health.isLoading ? "checking…" : (health.data?.status ?? "unreachable")}
        </span>
      </header>
      <main className="min-h-0 flex-1">
        {selectedProjectId ? <ProjectView projectId={selectedProjectId} /> : <ProjectList />}
      </main>
    </div>
  );
}
