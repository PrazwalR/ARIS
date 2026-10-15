import { useState } from "react";
import type { ReactNode } from "react";
import { useLive } from "../lib/liveContext";
import { PAGES, usePath } from "../lib/router";
import { Link } from "./Link";
import type { Theme } from "../lib/ui";

const REPO = "https://github.com/PrazwalR/ARIS";

export function Logo({ className }: { className?: string }) {
  // The wordmark is drawn as a mask so it takes the text colour in both themes.
  return <span className={`logo ${className ?? ""}`} role="img" aria-label="ARIS" />;
}

function ServerStatus() {
  const { status } = useLive();
  const label = { checking: "Checking", online: "Demo server online", offline: "Demo server offline" }[
    status
  ];
  return (
    <span className="server-status" data-state={status} data-testid="server-status">
      <span className="dot" aria-hidden="true" />
      {label}
    </span>
  );
}

export function Header({ theme, onTheme }: { theme: Theme; onTheme: (t: Theme) => void }) {
  const path = usePath();
  const [open, setOpen] = useState(false);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <header className="site-header">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="container header-row">
        <Link to="/" className="brand">
          <Logo />
        </Link>
        <nav aria-label="Main" className="site-nav" data-open={open}>
          {PAGES.map((p) => (
            <Link
              key={p.path}
              to={p.path}
              aria-current={path === p.path ? "page" : undefined}
              onNavigate={() => setOpen(false)}
            >
              {p.label}
            </Link>
          ))}
        </nav>
        <div className="header-tools">
          <ServerStatus />
          <button
            type="button"
            className="tool-button"
            onClick={() => onTheme(next)}
            aria-label={`Switch to ${next} theme`}
            data-testid="theme-toggle"
          >
            {theme === "dark" ? "Light" : "Dark"}
          </button>
          <button
            type="button"
            className="tool-button menu-button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? "Close" : "Menu"}
          </button>
        </div>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="container footer-row">
        <Logo className="logo-sm" />
        <nav aria-label="Documents" className="footer-links">
          <a href={`${REPO}/blob/main/docs/SECURITY.md`}>Security model</a>
          <a href={`${REPO}/blob/main/docs/METRICS.md`}>Metrics</a>
          <a href={`${REPO}/blob/main/docs/REVIEW_RUNBOOK.md`}>Review runbook</a>
          <a href={REPO}>Source</a>
        </nav>
      </div>
    </footer>
  );
}

/** Page title block: left-aligned, one line of context at most. */
export function PageHead({ title, lede, children }: { title: string; lede?: string; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1 tabIndex={-1}>{title}</h1>
        {lede ? <p className="lede">{lede}</p> : null}
      </div>
      {children ? <div className="page-head-aside">{children}</div> : null}
    </div>
  );
}

export function Panel(props: { title?: string; aside?: ReactNode; children: ReactNode; className?: string; testId?: string }) {
  return (
    <section className={`panel ${props.className ?? ""}`} data-testid={props.testId}>
      {props.title ? (
        <header className="panel-head">
          <h2>{props.title}</h2>
          {props.aside}
        </header>
      ) : null}
      <div className="panel-body">{props.children}</div>
    </section>
  );
}
