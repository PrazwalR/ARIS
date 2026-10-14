import { useId, useRef } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode } from "react";
import { HashChip, Icon } from "@aris/components/aris.esm.js";
import { useLive } from "../lib/liveContext";
import { useReveal } from "../lib/ui";

type Band = "plain" | "alt" | "tint";

export function Section(props: {
  id: string;
  eyebrow: string;
  title: string;
  intro?: ReactNode;
  band?: Band;
  children: ReactNode;
}) {
  const headingId = useId();
  const [ref, shown] = useReveal<HTMLDivElement>();
  return (
    <section
      id={props.id}
      className={`band band-${props.band ?? "plain"}`}
      aria-labelledby={headingId}
    >
      <div ref={ref} className="container reveal" data-shown={shown}>
        <header className="section-head">
          <p className="eyebrow">{props.eyebrow}</p>
          <h2 id={headingId} className="h1">
            {props.title}
          </h2>
          {props.intro ? <div className="body-l section-intro">{props.intro}</div> : null}
        </header>
        {props.children}
      </div>
    </section>
  );
}

/** Staggers its children's reveal by 70ms each. */
export function Stagger({ children, className }: { children: ReactNode[]; className?: string }) {
  return (
    <div className={className}>
      {children.map((child, i) => (
        <div key={i} className="stagger-item" style={{ "--i": i } as CSSProperties}>
          {child}
        </div>
      ))}
    </div>
  );
}

/** An account identifier, or -- under the privacy lens -- the risk_id the
 * network actually sees for it. */
export function AccountRef({ ifsc, account }: { ifsc: string; account: string }) {
  const { lens, riskIdFor } = useLive();
  if (!lens) {
    return (
      <span className="mono account-ref" data-testid="account-plain">
        {account} <span className="muted">· {ifsc}</span>
      </span>
    );
  }
  const riskId = riskIdFor(ifsc, account);
  return riskId ? (
    <HashChip value={riskId} label="risk_id" />
  ) : (
    <span className="mono-s muted" data-testid="account-hidden">
      risk_id pending…
    </span>
  );
}

export function OfflineNotice() {
  const { status, recheck } = useLive();
  if (status !== "offline") return null;
  return (
    <div className="notice" role="status" data-testid="offline-notice">
      <Icon name="info" />
      <div>
        <p className="body-s">
          <strong>The live demo server isn't running</strong>, so no decisions are shown. Nothing
          here is simulated: start it with <code className="mono-s">python -m aris.demo.server</code>{" "}
          and{" "}
          <button type="button" className="link-button" onClick={recheck}>
            check again
          </button>
          .
        </p>
      </div>
    </div>
  );
}

export interface TabOption<K extends string> {
  key: K;
  label: string;
}

/** WAI-ARIA tabs: arrow keys move between tabs, the panel follows. */
export function Tabs<K extends string>(props: {
  label: string;
  options: TabOption<K>[];
  value: K;
  onChange: (k: K) => void;
  panelId: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent, i: number) => {
    const n = props.options.length;
    const next = e.key === "ArrowRight" ? (i + 1) % n : e.key === "ArrowLeft" ? (i - 1 + n) % n : -1;
    if (next < 0) return;
    e.preventDefault();
    props.onChange(props.options[next].key);
    refs.current[next]?.focus();
  };
  return (
    <div className="tabs" role="tablist" aria-label={props.label}>
      {props.options.map((o, i) => {
        const selected = o.key === props.value;
        return (
          <button
            key={o.key}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`${props.panelId}-tab-${o.key}`}
            aria-selected={selected}
            aria-controls={props.panelId}
            tabIndex={selected ? 0 : -1}
            className="tab"
            onClick={() => props.onChange(o.key)}
            onKeyDown={(e) => onKey(e, i)}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** Icons the bundle doesn't ship, drawn to the same Lucide geometry:
 * 24 grid, 1.75 stroke, round caps and joins. */
const EXTRA: Record<string, string[]> = {
  sun: [
    "M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
    "M12 2v2",
    "M12 20v2",
    "m4.93 4.93 1.41 1.41",
    "m17.66 17.66 1.41 1.41",
    "M2 12h2",
    "M20 12h2",
    "m6.34 17.66-1.41 1.41",
    "m19.07 4.93-1.41 1.41",
  ],
  moon: ["M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"],
  server: ["M2 4h20v6H2z", "M2 14h20v6H2z", "M6 7h.01", "M6 17h.01"],
  gitBranch: ["M6 3v12", "M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M15 6a9 9 0 0 1-9 9"],
};

export function LineIcon({ name }: { name: keyof typeof EXTRA }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {EXTRA[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}

/** A horizontally scrollable table wrapper keyboard users can scroll too:
 * focusable, and named so a screen reader announces what it holds. */
export function TableWrap({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
