import { CheckIcon, ChevronDownIcon, XIcon } from "lucide-react";
import { PROJECT_ROLES, roleLabel } from "@cdip/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";

/**
 * The disciplines a project is being read for. Multi-select, because a job is
 * rarely one trade — and what is picked here is what summaries lead with
 * (workers/src/summarize.py `_role_focus`), so it is worth a moment's thought
 * rather than a single radio choice.
 */
export function RolesSelect({
  value,
  onChange,
  label = "Your role on this project",
  hint = "Summaries lead with what matters to the roles you pick. Nothing is hidden from you either way.",
}: {
  value: string[];
  onChange: (roles: string[]) => void;
  label?: string;
  hint?: string;
}) {
  const toggle = (role: string) =>
    onChange(value.includes(role) ? value.filter((r) => r !== role) : [...value, role]);

  return (
    <div className="grid gap-2">
      <Label htmlFor="roles-trigger">{label}</Label>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            id="roles-trigger"
            type="button"
            variant="outline"
            className="w-full justify-between font-normal"
          >
            <span className={value.length ? undefined : "text-muted-foreground"}>
              {value.length === 0
                ? "Select one or more roles"
                : value.length === 1
                  ? roleLabel(value[0]!)
                  : `${value.length} roles selected`}
            </span>
            <ChevronDownIcon className="opacity-50" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="max-h-72 w-(--radix-dropdown-menu-trigger-width) overflow-y-auto"
        >
          <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
            Pick every discipline you are responsible for
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {PROJECT_ROLES.map((role) => (
            <DropdownMenuCheckboxItem
              key={role.value}
              checked={value.includes(role.value)}
              // Radix closes on select by default; a multi-select must not.
              onSelect={(event) => event.preventDefault()}
              onCheckedChange={() => toggle(role.value)}
            >
              {role.label}
            </DropdownMenuCheckboxItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      {value.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {value.map((role) => (
            <Badge key={role} variant="secondary" className="gap-1 py-1 pr-1">
              <CheckIcon className="size-3" />
              {roleLabel(role)}
              <button
                type="button"
                className="hover:bg-background/60 rounded-sm p-0.5"
                onClick={() => toggle(role)}
                aria-label={`Remove ${roleLabel(role)}`}
              >
                <XIcon className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
    </div>
  );
}
