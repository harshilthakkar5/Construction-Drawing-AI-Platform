import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { API_URL, api, authToken } from "./api";
import { AuthScreen } from "./components/AuthScreen";
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
  const queryClient = useQueryClient();
  const selectedProjectId = useAppStore((s) => s.selectedProjectId);
  const user = useAppStore((s) => s.user);
  const setUser = useAppStore((s) => s.setUser);
  const openProject = useAppStore((s) => s.openProject);
  const [checkingSession, setCheckingSession] = useState(Boolean(authToken.get()));

  // Restore the session from a stored token on load.
  useEffect(() => {
    if (!authToken.get()) return;
    api
      .me()
      .then(setUser)
      .catch(() => authToken.clear())
      .finally(() => setCheckingSession(false));
  }, [setUser]);

  async function signOut() {
    await api.logout().catch(() => {});
    authToken.clear();
    setUser(null);
    openProject(null);
    queryClient.clear();
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-gray-200 px-4 py-2">
        <h1 className="text-lg font-semibold">Construction Drawing AI Platform</h1>
        <div className="flex items-center gap-4 text-sm text-gray-500">
          <span>
            API: {health.isLoading ? "checking…" : (health.data?.status ?? "unreachable")}
          </span>
          {user && (
            <>
              <span className="text-gray-700">{user.name}</span>
              <button className="text-blue-600 hover:underline" onClick={() => void signOut()}>
                Sign out
              </button>
            </>
          )}
        </div>
      </header>
      <main className="min-h-0 flex-1">
        {checkingSession ? (
          <div className="flex h-full items-center justify-center text-sm text-gray-500">
            Restoring session…
          </div>
        ) : !user ? (
          <AuthScreen onSignedIn={setUser} />
        ) : selectedProjectId ? (
          <ProjectView projectId={selectedProjectId} />
        ) : (
          <ProjectList />
        )}
      </main>
    </div>
  );
}
