import { useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { HashChip } from "@aris/components/aris.esm.js";
import { useLive } from "../lib/liveContext";

/** An account identifier, or -- in network view -- the pseudonymous risk_id
 * the network actually sees for it. */
export function AccountRef({ ifsc, account }: { ifsc: string; account: string }) {
  const { lens, riskIdFor } = useLive();
  if (!lens) {
    return (
      <span className="mono" data-testid="account-plain">
        {account} <span className="muted">· {ifsc}</span>
      </span>
    );
  }
  const riskId = riskIdFor(ifsc, account);
  return riskId ? (
    <HashChip value={riskId} label="risk_id" />
  ) : (
    <span className="mono muted" data-testid="account-hidden">
      deriving…
    </span>
  );
}

export function OfflineNotice() {
  const { status, recheck } = useLive();
  if (status !== "offline") return null;
  return (
    <div className="notice" role="status" data-testid="offline-notice">
      <strong>Demo server not running.</strong> No decisions are shown; nothing here is simulated.
      Start it with <code className="mono">python -m aris.demo.server</code>, then{" "}
      <button type="button" className="link-button" onClick={recheck}>
        retry
      </button>
      .
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

/** A horizontally scrollable table wrapper keyboard users can scroll too. */
export function TableWrap({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
