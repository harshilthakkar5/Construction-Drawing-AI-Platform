import {
  ChevronsUpDownIcon,
  LayoutDashboardIcon,
  LifeBuoyIcon,
  LogOutIcon,
  FolderOpenIcon,
  UserIcon,
} from "lucide-react";
import type { UserDto } from "@cdip/shared";
import { LogoMark } from "@/components/Logo";
import { ModeToggle } from "@/components/theme-provider";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Separator } from "@/components/ui/separator";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";
import { useAppStore, type View } from "@/store";

/**
 * Signed-in chrome: shadcn's inset sidebar (brand, grouped nav, user menu) and
 * a sticky page header. Navigation is store state, not a router — `view` plus
 * selectedProjectId is the whole location — so the nav items set `view`
 * directly and mark themselves active off it.
 */
const NAV: { group: string; items: { view: View; label: string; icon: React.ElementType }[] }[] = [
  {
    group: "Workspace",
    items: [
      { view: "dashboard", label: "Dashboard", icon: LayoutDashboardIcon },
      { view: "projects", label: "Projects", icon: FolderOpenIcon },
    ],
  },
  {
    group: "Account",
    items: [
      { view: "account", label: "Account", icon: UserIcon },
      { view: "support", label: "Support", icon: LifeBuoyIcon },
    ],
  },
];

const TITLES: Record<View, string> = {
  dashboard: "Dashboard",
  projects: "Projects",
  account: "Account",
  support: "Support",
};

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
  const selectedProjectId = useAppStore((s) => s.selectedProjectId);

  return (
    <SidebarProvider className="h-svh min-h-0 overflow-hidden">
      <AppSidebar user={user} onSignOut={onSignOut} />
      <SidebarInset className="min-h-0">
        <header className="bg-background/80 sticky top-0 z-20 flex h-14 shrink-0 items-center gap-2 border-b px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-1 h-4" />
          <span className="text-sm font-medium">
            {selectedProjectId ? "Projects" : TITLES[view]}
          </span>
          {selectedProjectId && (
            <>
              <span className="text-muted-foreground/60 text-sm">/</span>
              <span className="text-muted-foreground text-sm">Project</span>
            </>
          )}
          <div className="ml-auto flex items-center gap-1">
            <ModeToggle />
            <UserMenu user={user} onSignOut={onSignOut} align="end" compact />
          </div>
        </header>
        {/* Pages own their scrolling so the project view can fill the height. */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
      </SidebarInset>
    </SidebarProvider>
  );
}

function AppSidebar({ user, onSignOut }: { user: UserDto; onSignOut: () => void }) {
  const view = useAppStore((s) => s.view);
  const setView = useAppStore((s) => s.setView);
  const { isMobile, setOpenMobile } = useSidebar();

  const go = (next: View) => {
    setView(next);
    if (isMobile) setOpenMobile(false);
  };

  return (
    <Sidebar variant="inset" collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              size="lg"
              onClick={() => go("dashboard")}
              className="group-data-[collapsible=icon]:p-1.5!"
            >
              {/* Wrapped: the menu-button variant forces direct-child svgs to 16px. */}
              <div className="flex shrink-0 items-center">
                <LogoMark className="size-8 rounded-md" />
              </div>
              <div className="grid flex-1 text-left leading-tight">
                <span className="truncate font-semibold">ArcAligned AI</span>
                <span className="text-muted-foreground truncate text-xs">Drawing intelligence</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        {NAV.map((group) => (
          <SidebarGroup key={group.group}>
            <SidebarGroupLabel>{group.group}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => (
                  <SidebarMenuItem key={item.view}>
                    <SidebarMenuButton
                      isActive={view === item.view}
                      tooltip={item.label}
                      onClick={() => go(item.view)}
                    >
                      <item.icon />
                      <span>{item.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <UserMenu user={user} onSignOut={onSignOut} align="start" side="right" />
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}

/**
 * The account menu. Rendered twice — as the sidebar footer card and as the
 * header avatar (`compact`) — because the sidebar can be collapsed away.
 */
function UserMenu({
  user,
  onSignOut,
  align,
  side,
  compact = false,
}: {
  user: UserDto;
  onSignOut: () => void;
  align: "start" | "end";
  side?: "right" | "bottom";
  compact?: boolean;
}) {
  const setView = useAppStore((s) => s.setView);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {compact ? (
          <button className="focus-visible:ring-ring/50 rounded-full outline-none focus-visible:ring-[3px]">
            <InitialsAvatar name={user.name} className="size-8" />
            <span className="sr-only">Open account menu</span>
          </button>
        ) : (
          <SidebarMenuButton
            size="lg"
            className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
          >
            <InitialsAvatar name={user.name} />
            <div className="grid flex-1 text-left text-sm leading-tight">
              <span className="truncate font-medium">{user.name}</span>
              <span className="text-muted-foreground truncate text-xs">{user.email}</span>
            </div>
            <ChevronsUpDownIcon className="ml-auto size-4" />
          </SidebarMenuButton>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent
        className="w-56 rounded-lg"
        align={align}
        side={side}
        sideOffset={4}
      >
        <DropdownMenuLabel className="p-0 font-normal">
          <div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
            <InitialsAvatar name={user.name} />
            <div className="grid flex-1 leading-tight">
              <span className="truncate font-medium">{user.name}</span>
              <span className="text-muted-foreground truncate text-xs">{user.email}</span>
            </div>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onClick={() => setView("account")}>
            <UserIcon />
            Account settings
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setView("support")}>
            <LifeBuoyIcon />
            Support
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onClick={onSignOut}>
          <LogOutIcon />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Initials avatar — the app has no uploaded profile images. */
export function InitialsAvatar({ name, className }: { name: string; className?: string }) {
  const initials =
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join("") || "?";
  return (
    <Avatar className={cn("size-8 rounded-lg", className)}>
      <AvatarFallback className="bg-primary text-primary-foreground rounded-lg font-medium">
        {initials}
      </AvatarFallback>
    </Avatar>
  );
}
