import type { Mode } from "../../scene/deriveScene";

export function modeFromSearch(search: string): Mode {
  return new URLSearchParams(search).get("mode") === "narrator" ? "narrator" : "villager";
}

export function urlWithMode(href: string, mode: Mode): string {
  const url = new URL(href);
  url.searchParams.set("mode", mode);
  return `${url.pathname}${url.search}${url.hash}`;
}
