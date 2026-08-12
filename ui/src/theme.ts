import { useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";

const KEY = "echomind.theme";
const CHOICES: readonly ThemeChoice[] = ["system", "light", "dark"];

function stored(): ThemeChoice {
  const value = localStorage.getItem(KEY);
  return CHOICES.includes(value as ThemeChoice) ? (value as ThemeChoice) : "system";
}

/**
 * Keeps the `data-theme` attribute on <html> in step with the reader's choice, and
 * guarantees the choice can override the operating system in both directions.
 *
 * A single dark-mode switch cannot do that. This tool is demonstrated on a laptop whose
 * OS follows sunset while the room it is projected in does not, so "force light" has to
 * be as reachable as "force dark" — and "system" has to remain reachable after either,
 * or the app stops tracking the machine it runs on for good.
 */
export function useTheme(): [ThemeChoice, (next: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(stored);

  useEffect(() => {
    const root = document.documentElement;
    if (choice === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
    localStorage.setItem(KEY, choice);
  }, [choice]);

  return [choice, setChoice];
}
