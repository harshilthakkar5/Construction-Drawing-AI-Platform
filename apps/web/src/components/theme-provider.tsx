import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { CheckIcon, MonitorIcon, MoonIcon, SunIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/**
 * Light / dark / system, applied as the `.dark` class on <html> — the class
 * every token in index.css keys off. "system" follows the OS and keeps
 * following it: the media query stays subscribed rather than being read once.
 */
export type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "cdip-theme";

/** Colour palette, separate from light/dark: "teal" is the new default, "neutral" the original. */
export type Palette = "blueprint" | "teal" | "neutral";
const PALETTE_KEY = "cdip-palette";
/** The saved palette, read before the first paint (main.tsx) and by the provider. */
export const readPalette = (): Palette => {
  const saved = localStorage.getItem(PALETTE_KEY);
  return saved === "teal" || saved === "neutral" ? saved : "blueprint";
};
const PALETTES: { id: Palette; label: string }[] = [
  { id: "blueprint", label: "Blueprint" },
  { id: "teal", label: "Teal" },
  { id: "neutral", label: "Neutral (original)" },
];

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  /** What is actually on screen once "system" is resolved. */
  resolved: "light" | "dark";
  palette: Palette;
  setPalette: (palette: Palette) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

const prefersDark = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(
    () => (localStorage.getItem(STORAGE_KEY) as Theme | null) ?? "system",
  );
  const [systemDark, setSystemDark] = useState(prefersDark);
  const [palette, setPaletteState] = useState<Palette>(readPalette);

  useEffect(() => {
    document.documentElement.dataset.palette = palette;
  }, [palette]);

  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystemDark(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const resolved = theme === "system" ? (systemDark ? "dark" : "light") : theme;

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", resolved === "dark");
    root.style.colorScheme = resolved;
  }, [resolved]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      theme,
      resolved,
      setTheme: (next) => {
        localStorage.setItem(STORAGE_KEY, next);
        setThemeState(next);
      },
      palette,
      setPalette: (next) => {
        localStorage.setItem(PALETTE_KEY, next);
        setPaletteState(next);
      },
    }),
    [theme, resolved, palette],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used within a ThemeProvider.");
  return context;
}

/** Header control: sun/moon button with an explicit System option. */
export function ModeToggle() {
  const { theme, setTheme, resolved, palette, setPalette } = useTheme();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Change theme">
          {resolved === "dark" ? <MoonIcon /> : <SunIcon />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem onClick={() => setTheme("light")} data-active={theme === "light"}>
          <SunIcon /> Light
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("dark")} data-active={theme === "dark"}>
          <MoonIcon /> Dark
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme("system")} data-active={theme === "system"}>
          <MonitorIcon /> System
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">Colours</DropdownMenuLabel>
        {PALETTES.map((p) => (
          <DropdownMenuItem key={p.id} onClick={() => setPalette(p.id)} data-active={palette === p.id}>
            <CheckIcon className={palette === p.id ? "" : "invisible"} /> {p.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
