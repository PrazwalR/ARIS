import { useState } from "react";
import { LineIcon } from "../components/common";
import type { Theme } from "../lib/ui";

const LINKS = [
  ["#how", "How it works"],
  ["#console", "Try it"],
  ["#federated", "Federated learning"],
  ["#metrics", "Metrics"],
  ["#security", "Security"],
  ["#proof", "Proof"],
] as const;

export function Header({ theme, onTheme }: { theme: Theme; onTheme: (t: Theme) => void }) {
  const [open, setOpen] = useState(false);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <header className="site-header">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="container header-row">
        <a className="wordmark" href="#top" aria-label="ARIS, back to top">
          ARIS
        </a>
        <nav aria-label="Sections" className="site-nav" data-open={open}>
          <ul>
            {LINKS.map(([href, label]) => (
              <li key={href}>
                <a href={href} onClick={() => setOpen(false)}>
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="header-actions">
          <button
            type="button"
            className="icon-button"
            onClick={() => onTheme(next)}
            aria-label={`Switch to ${next} theme`}
            data-testid="theme-toggle"
          >
            <LineIcon name={theme === "dark" ? "sun" : "moon"} />
          </button>
          <button
            type="button"
            className="icon-button menu-button"
            aria-expanded={open}
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen(!open)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              {open ? (
                <path d="M6 6l12 12M18 6 6 18" />
              ) : (
                <path d="M4 7h16M4 12h16M4 17h16" />
              )}
            </svg>
          </button>
        </div>
      </div>
    </header>
  );
}
