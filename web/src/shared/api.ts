import { useQuery } from "@tanstack/react-query";
import type { Console } from "./types";

export const CONSOLE_URL = "data/console.json";

export async function fetchConsole(): Promise<Console> {
  const res = await fetch(CONSOLE_URL);
  if (!res.ok) throw new Error(`console data: HTTP ${res.status}`);
  return res.json();
}

export function useConsole() {
  return useQuery({ queryKey: ["console"], queryFn: fetchConsole, retry: false });
}
