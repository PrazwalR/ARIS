import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";
import { Button, GlassCard, HashChip, StatusPill } from "@aris/components/aris.esm.js";
import { OfflineNotice, Section } from "../components/common";
import { api, ApiError, pillDecision } from "../lib/api";
import type { AuditRecord, FeedRow, TransferResult } from "../lib/api";
import { isValidIfsc, parseRupees, rupees, shortTime } from "../lib/format";
import { useLive } from "../lib/liveContext";

const FEED_POLL_MS = 3000;

export function Console() {
  return (
    <Section
      id="console"
      eyebrow="Try it"
      title="Send a transfer of your own"
      intro="Every decision below comes from BankBot over HTTP. Flag any account as Bank B, then send money to it."
    >
      <OfflineNotice />
      <div className="console-grid">
        <TransferForm />
        <DecisionFeed />
      </div>
      <AnalystPanel />
    </Section>
  );
}

function TransferForm() {
  const live = useLive();
  const ids = { ifsc: useId(), account: useId(), amount: useId() };
  const [ifsc, setIfsc] = useState("HDFC0001234");
  const [account, setAccount] = useState("ACC-12345");
  const [amount, setAmount] = useState("2,500");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TransferResult | null>(null);
  const [flagged, setFlagged] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const ifscOk = isValidIfsc(ifsc);
  const amountMinor = parseRupees(amount);
  const accountOk = account.trim().length > 0;
  const valid = ifscOk && accountOk && amountMinor !== null;
  const disabled = live.status !== "online" || busy || !valid;

  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const send = (e: FormEvent) => {
    e.preventDefault();
    if (disabled || amountMinor === null) return;
    void act(async () => {
      const r = await api.transfer({
        receiver_ifsc: ifsc.trim().toUpperCase(),
        receiver_account: account.trim(),
        amount_minor: amountMinor,
      });
      setResult(r);
      live.setLastAuditRef(r.audit_ref);
      live.bumpFeed();
    });
  };

  const flag = () =>
    act(async () => {
      const r = await api.flag(ifsc.trim().toUpperCase(), account.trim());
      setFlagged(r.network.score !== null ? `${r.publisher} · score ${r.network.score}` : r.outcome);
    });

  return (
    <GlassCard lift={false} className="console-card">
      <h3 className="ar-card-title">Transfer at Bank A</h3>
      <form className="form" onSubmit={send} noValidate>
        <div className="field">
          <label htmlFor={ids.ifsc} className="label">
            Receiver IFSC
          </label>
          <input
            id={ids.ifsc}
            className="input mono"
            value={ifsc}
            onChange={(e) => setIfsc(e.target.value)}
            aria-invalid={!ifscOk}
            aria-describedby={ifscOk ? undefined : `${ids.ifsc}-err`}
            autoComplete="off"
            spellCheck={false}
          />
          {!ifscOk ? (
            <p id={`${ids.ifsc}-err`} className="field-error">
              Four letters, a zero, then six letters or digits, e.g. HDFC0001234.
            </p>
          ) : null}
        </div>
        <div className="field">
          <label htmlFor={ids.account} className="label">
            Receiver account
          </label>
          <input
            id={ids.account}
            className="input mono"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            aria-invalid={!accountOk}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="field">
          <label htmlFor={ids.amount} className="label">
            Amount (₹)
          </label>
          <input
            id={ids.amount}
            className="input mono"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            aria-invalid={amountMinor === null}
            aria-describedby={amountMinor === null ? `${ids.amount}-err` : undefined}
          />
          {amountMinor === null ? (
            <p id={`${ids.amount}-err`} className="field-error">
              A positive amount with at most two decimal places.
            </p>
          ) : null}
        </div>
        <div className="form-actions">
          <Button type="submit" variant="secondary" disabled={disabled}>
            Send {amountMinor !== null ? rupees(amountMinor) : ""}
          </Button>
          <Button variant="ghost" size="sm" onClick={flag} disabled={disabled}>
            Flag as Bank B
          </Button>
        </div>
      </form>
      <div aria-live="polite" className="console-result" data-testid="console-result">
        {flagged ? <p className="body-s">Bank B published a flag: {flagged}.</p> : null}
        {result ? (
          <div className="result-block">
            <StatusPill decision={pillDecision(result.decision)} size="lg" />
            <p className="body-s">{result.user_message}</p>
            <HashChip value={result.audit_ref} label="audit_ref" head={8} tail={4} />
          </div>
        ) : null}
        {error ? (
          <p className="body-s error-text" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </GlassCard>
  );
}

function DecisionFeed() {
  const live = useLive();
  const [rows, setRows] = useState<FeedRow[]>([]);

  useEffect(() => {
    if (live.status !== "online") return;
    let alive = true;
    const load = () =>
      api
        .feed()
        .then((r) => alive && setRows(r.decisions))
        .catch(() => undefined);
    load();
    const id = setInterval(load, FEED_POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [live.status, live.feedVersion]);

  return (
    <GlassCard lift={false} className="console-card">
      <h3 className="ar-card-title">BankBot decision log</h3>
      <p className="body-s muted">
        What an operator sees: decisions keyed by pseudonymous id. No account numbers, by
        construction.
      </p>
      {rows.length === 0 ? (
        <p className="body-s muted feed-empty">No decisions yet.</p>
      ) : (
        <ul className="feed" data-testid="feed">
          {rows.map((r) => (
            <li key={r.audit_ref} className="feed-row">
              <StatusPill decision={pillDecision(r.decision)} />
              <span className="mono-s">{rupees(r.amount_minor)}</span>
              <HashChip value={r.risk_id || "unavailable"} label="risk_id" />
              <span className="mono-s muted">{shortTime(r.decided_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </GlassCard>
  );
}

function AnalystPanel() {
  const live = useLive();
  const ids = { id: useId(), key: useId(), reason: useId(), ref: useId() };
  // Edits are derived over the server's defaults rather than copied into
  // state by an effect: the investigator id defaults to the one the server
  // reports, and the reference follows the latest transfer until edited.
  const [investigatorEdit, setInvestigator] = useState<string | null>(null);
  const investigator = investigatorEdit ?? live.investigatorId ?? "";
  const [key, setKey] = useState("");
  const [reason, setReason] = useState("Review of blocked transfer");
  const [refEdit, setRefEdit] = useState<{ base: string | null; value: string } | null>(null);
  const ref = refEdit && refEdit.base === live.lastAuditRef ? refEdit.value : (live.lastAuditRef ?? "");
  const setRef = (value: string) => setRefEdit({ base: live.lastAuditRef, value });
  const [record, setRecord] = useState<AuditRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setRecord(null);
    try {
      setRecord(await api.audit(ref.trim(), { id: investigator.trim(), key, reason }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Investigator credentials rejected (401). The attempt was not logged as a read.");
      } else if (err instanceof ApiError && err.status === 404) {
        setError("No audit record for that reference.");
      } else {
        setError(err instanceof ApiError ? err.message : String(err));
      }
    } finally {
      setBusy(false);
    }
  };

  const disabled =
    live.status !== "online" || busy || !investigator.trim() || !key || !reason.trim() || !ref.trim();

  return (
    <GlassCard lift={false} className="analyst-card">
      <p className="ar-card-eyebrow">Analyst view</p>
      <h3 className="ar-card-title">Read the evidence behind a decision</h3>
      <p className="body-s muted">
        Only a named investigator with a stated reason can read a record. The demo server prints
        the investigator key when it starts.
      </p>
      <form className="form analyst-form" onSubmit={submit}>
        <div className="field">
          <label htmlFor={ids.id} className="label">
            Investigator
          </label>
          <input id={ids.id} className="input mono" value={investigator} onChange={(e) => setInvestigator(e.target.value)} autoComplete="username" />
        </div>
        <div className="field">
          <label htmlFor={ids.key} className="label">
            Key
          </label>
          <input id={ids.key} className="input mono" type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="current-password" />
        </div>
        <div className="field">
          <label htmlFor={ids.reason} className="label">
            Reason
          </label>
          <input id={ids.reason} className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={ids.ref} className="label">
            Audit reference
          </label>
          <input id={ids.ref} className="input mono" value={ref} onChange={(e) => setRef(e.target.value)} spellCheck={false} />
        </div>
        <div className="form-actions">
          <Button type="submit" variant="secondary" icon="key" disabled={disabled}>
            Open record
          </Button>
        </div>
      </form>
      <div aria-live="polite" data-testid="audit-result">
        {error ? (
          <p className="body-s error-text" role="alert">
            {error}
          </p>
        ) : null}
        {record ? (
          <dl className="record">
            <div>
              <dt className="label">Decision</dt>
              <dd>
                <StatusPill decision={pillDecision(record.decision)} />
              </dd>
            </div>
            <div>
              <dt className="label">Network score</dt>
              <dd className="mono" data-testid="audit-score">
                {record.risk_score ?? "none"}
              </dd>
            </div>
            <div>
              <dt className="label">Flagged by</dt>
              <dd className="mono">{record.contributing_banks.join(", ") || "none"}</dd>
            </div>
            <div>
              <dt className="label">Thresholds in force</dt>
              <dd className="mono" data-testid="audit-policy">
                step-up ≥ {record.policy.step_up_at} · block ≥ {record.policy.block_at}
              </dd>
            </div>
            <div>
              <dt className="label">Reason codes</dt>
              <dd className="mono-s">{record.reason_codes.join(", ") || "none"}</dd>
            </div>
            <div>
              <dt className="label">risk_id</dt>
              <dd>
                <HashChip value={record.risk_id || "unavailable"} />
              </dd>
            </div>
          </dl>
        ) : null}
      </div>
    </GlassCard>
  );
}
