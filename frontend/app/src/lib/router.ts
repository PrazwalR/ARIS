import { useEffect, useRef, useSyncExternalStore } from "react";

/** Pages, in navigation order. `/live` rather than `/demo`: `/demo/*` is the
 * showcase server's API prefix. */
export const PAGES = [
  { path: "/", label: "Overview" },
  { path: "/live", label: "Live demo" },
  { path: "/model", label: "Model" },
  { path: "/security", label: "Security" },
  { path: "/evidence", label: "Evidence" },
] as const;

const CHANGE = "aris:navigate";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener(CHANGE, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(CHANGE, onChange);
  };
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname);
}

export function navigate(to: string): void {
  if (to === window.location.pathname) return;
  window.history.pushState(null, "", to);
  window.dispatchEvent(new Event(CHANGE));
}

/** Moves focus to the new page's heading and resets scroll, so keyboard and
 * screen-reader users land on the content a navigation produced. */
export function usePageFocus(path: string): void {
  const first = useRef(true);
  useEffect(() => {
    // Only after a navigation: on first load the browser's own focus order
    // (skip link first) must stay intact.
    if (first.current) {
      first.current = false;
      return;
    }
    window.scrollTo(0, 0);
    document.querySelector<HTMLElement>("main h1")?.focus({ preventScroll: true });
  }, [path]);
}
