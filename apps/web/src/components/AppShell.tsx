import { useState } from "react";
import type { UserDto } from "@cdip/shared";
import { useAppStore, type View } from "../store";
import { Logo } from "./Logo";

/**
 * Signed-in chrome: collapsible sidebar (logo, nav, user card) + the page.
 * Navigation is store state, not a router — `view` plus selectedProjectId is
 * the whole location.
 */
const NAV: { view: View; label: string; icon: React.ReactNode }[] = [
  { view: "dashboard", label: "Dashboard", icon: <GridIcon /> },
  { view: "projects", label: "Projects", icon: <FilesIcon /> },
  { view: "support", label: "Support", icon: <LifebuoyIcon /> },
];

export function AppShell({
  user,
  onSignOut,
  children,
}: {
  user: UserDto;
  onSignOut: () => void;
  children: React.ReactNode;
}) {
  const view = useAppStore((s) => s.view);
  const setView = useAppStore((s) => s.setView);
  const [collapsed, setCollapsed] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="flex h-full">
      <aside
        className={`flex shrink-0 flex-col border-r border-hairline bg-surface transition-[width] duration-200 ${
          collapsed ? "w-[68px]" : "w-64"
        }`}
      >
        <div className="flex items-center justify-between px-4 py-4">
          {collapsed ? <Logo compact /> : <Logo />}
          <button
            className="rounded p-1 text-ink-muted hover:bg-page hover:text-ink"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <PanelIcon collapsed={collapsed} />
          </button>
        </div>

        <nav className="flex-1 space-y-1 px-3">
          {NAV.map((item) => (
            <button
              key={item.view}
              onClick={() => setView(item.view)}
              title={item.label}
              aria-current={view === item.view ? "page" : undefined}
              className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${
                view === item.view
                  ? "bg-brand-50 font-semibold text-brand-700"
                  : "text-ink-soft hover:bg-page hover:text-ink"
              }`}
            >
              <span className="shrink-0">{item.icon}</span>
              {!collapsed && <span className="truncate">{item.label}</span>}
            </button>
          ))}
        </nav>

        <div className="relative border-t border-hairline p-3">
          {menuOpen && (
            <div className="absolute bottom-full left-3 right-3 mb-2 overflow-hidden rounded-lg border border-hairline bg-surface shadow-lg">
              <button
                className="block w-full px-3 py-2 text-left text-sm text-ink-soft hover:bg-page"
                onClick={() => {
                  setMenuOpen(false);
                  setView("account");
                }}
              >
                Account settings
              </button>
              <button
                className="block w-full px-3 py-2 text-left text-sm text-red-600 hover:bg-page"
                onClick={() => {
                  setMenuOpen(false);
                  onSignOut();
                }}
              >
                Sign out
              </button>
            </div>
          )}
          <button
            className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left hover:bg-page"
            onClick={() => setMenuOpen((o) => !o)}
            aria-expanded={menuOpen}
          >
            <Avatar name={user.name} />
            {!collapsed && (
              <>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink">{user.name}</span>
                  <span className="block truncate text-xs text-ink-muted">{user.email}</span>
                </span>
                <ChevronsIcon />
              </>
            )}
          </button>
        </div>
      </aside>

      {/* Pages own their scrolling so the project view can fill the height. */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</main>
    </div>
  );
}

export function Avatar({ name, size = 32 }: { name: string; size?: number }) {
  const initials =
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || "?";
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full bg-brand-100 font-semibold text-brand-700"
      style={{ width: size, height: size, fontSize: size * 0.38 }}
      aria-hidden
    >
      {initials}
    </span>
  );
}

const nav = {
  width: 18,
  height: 18,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

function GridIcon() {
  return (
    <svg {...nav}>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  );
}
function FilesIcon() {
  return (
    <svg {...nav}>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 7h4M8 11h8M8 15h8" />
    </svg>
  );
}
function LifebuoyIcon() {
  return (
    <svg {...nav}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="3.5" />
      <path d="m5.6 5.6 3.9 3.9M14.5 14.5l3.9 3.9M18.4 5.6l-3.9 3.9M9.5 14.5l-3.9 3.9" />
    </svg>
  );
}
function PanelIcon({ collapsed }: { collapsed: boolean }) {
  return (
    <svg {...nav}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
      <path d={collapsed ? "m14 9 3 3-3 3" : "m17 9-3 3 3 3"} />
    </svg>
  );
}
function ChevronsIcon() {
  return (
    <svg {...nav} width={14} height={14}>
      <path d="m8 9 4-4 4 4M8 15l4 4 4-4" />
    </svg>
  );
}
