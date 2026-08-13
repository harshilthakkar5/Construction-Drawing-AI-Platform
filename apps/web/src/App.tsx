import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, authToken } from "@/api";
import { AppShell } from "@/components/AppShell";
import { AuthScreen } from "@/components/AuthScreen";
import { ProjectView } from "@/components/ProjectView";
import { PageLoading } from "@/components/shared";
import { AccountPage } from "@/pages/AccountPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { ProjectsPage } from "@/pages/ProjectsPage";
import { SupportPage } from "@/pages/SupportPage";
import { useAppStore } from "@/store";

export function App() {
  const queryClient = useQueryClient();
  const view = useAppStore((s) => s.view);
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

  if (checkingSession) {
    return (
      <div className="grid h-full place-items-center">
        <PageLoading label="Restoring session…" />
      </div>
    );
  }
  if (!user) return <AuthScreen onSignedIn={setUser} />;

  return (
    <AppShell user={user} onSignOut={() => void signOut()}>
      {/* An open project takes over the Projects area (FR-17 three-pane view). */}
      {selectedProjectId ? (
        <ProjectView projectId={selectedProjectId} />
      ) : view === "projects" ? (
        <ProjectsPage />
      ) : view === "support" ? (
        <SupportPage />
      ) : view === "account" ? (
        <AccountPage />
      ) : (
        <DashboardPage />
      )}
    </AppShell>
  );
}
