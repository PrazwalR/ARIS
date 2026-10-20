import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { HashChip, StatusPill } from "@aris/components/aris.esm.js";
import { AccountRef, OfflineNotice, TableWrap } from "../components/common";
import { PageHead, Panel } from "../components/Layout";
import { api, ApiError, pillDecision } from "../lib/api";
import type { AuditRecord, Decision, FeedRow, FlagResult, ForgeResult, TransferResult } from "../lib/api";
import { isValidIfsc, parseRupees, rupees, shortTime } from "../lib/format";
import { useLive } from "../lib/liveContext";
import { CASES, STORY } from "../lib/story";
import { prefersReducedMotion } from "../lib/ui";

const FEED_POLL_MS = 3000;
const WORD = { allow: "Allowed", stepup: "Step-up", block: "Blocked" } as const;

export function Live() {
  const live = useLive();
  return (
    <div className="container">
      <PageHead title="Live demo" lede="Every decision on this page is made by BankBot over HTTP.">
        <label className="check">
          <input
            type="checkbox"
            checked={live.lens}
            onChange={(e) => live.setLens(e.target.checked)}
            data-testid="network-view"
          />
          Network view
          <span className="muted"> · show pseudonymous IDs</span>
        </label>
        <OutageSwitch />
      </PageHead>
      <OfflineNotice />
      {live.busDown ? (
        <p className="notice notice-warn" role="status" data-testid="outage-banner">
          Bus outage on: every lookup answers <span className="mono">unavailable</span>. BankBot fails closed, so
          nothing is allowed blind.
        </p>
      ) : null}
      <Scenario />
      <PolicyCases />
      <div className="grid-2">
        <Sandbox />
        <DecisionLog />
      </div>
      <Investigator />
    </div>
  );
}

function useAction() {
  const live = useLive();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run<T>(fn: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      if (e instanceof ApiError && e.status === 0) live.recheck();
      return null;
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, run };
}

function OutageSwitch() {
  const live = useLive();
  const [busy, setBusy] = useState(false);
  return (
    <label className="check">
      <input
        type="checkbox"
        checked={live.busDown}
        disabled={live.status !== "online" || busy}
        onChange={(e) => {
          setBusy(true);
          live
            .setBusDown(e.target.checked)
            .catch(() => live.recheck())
            .finally(() => setBusy(false));
        }}
        data-testid="outage"
      />
      Bus outage
      <span className="muted"> · fault injection</span>
    </label>
  );
}

interface CaseOutcome {
  actual: Decision;
  auditRef: string;
}

/** Every policy branch, run against the real API: publish (if any), forge (if
 * any), outage (if any), then the transfer. "Expected" is the rule; "BankBot"
 * is what the server decided. */
function PolicyCases() {
  const live = useLive();
  const { busy, error, run } = useAction();
  const [outcomes, setOutcomes] = useState<Record<string, CaseOutcome>>({});
  const [current, setCurrent] = useState<string | null>(null);

  const runAll = () =>
    run(async () => {
      setOutcomes({});
      await api.reset();
      try {
        for (const c of CASES) {
          setCurrent(c.id);
          if (c.flag) await api.flag(STORY.ifsc, c.account, c.flag);
          if (c.forge) await api.forge(STORY.ifsc, c.account);
          if (c.outage) await live.setBusDown(true);
          const r = await api.transfer({
            receiver_ifsc: STORY.ifsc,
            receiver_account: c.account,
            amount_minor: c.amountMinor,
          });
          if (c.outage) await live.setBusDown(false);
          live.setLastAuditRef(r.audit_ref);
          setOutcomes((prev) => ({ ...prev, [c.id]: { actual: r.decision, auditRef: r.audit_ref } }));
          live.bumpFeed();
        }
      } finally {
        setCurrent(null);
        if (live.busDown) await live.setBusDown(false);
      }
    });

  const done = Object.keys(outcomes).length;
  const matched = CASES.filter((c) => outcomes[c.id]?.actual === c.expect).length;
  const locked = live.status !== "online" || busy;

  return (
    <Panel
      title="Policy cases"
      testId="policy-cases"
      aside={
        <div className="panel-actions">
          {done ? (
            <span className="small muted" data-testid="cases-summary">
              {matched}/{CASES.length} match
            </span>
          ) : null}
          <button type="button" className="btn btn-sm btn-primary" onClick={runAll} disabled={locked}>
            {busy ? "Running…" : "Run all cases"}
          </button>
        </div>
      }
    >
      <TableWrap label="Policy cases">
        <table className="data compact">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Case</th>
              <th scope="col" className="num">Amount</th>
              <th scope="col">Rule</th>
              <th scope="col">Expected</th>
              <th scope="col">BankBot</th>
              <th scope="col">Audit ref</th>
            </tr>
          </thead>
          <tbody>
            {CASES.map((c, i) => {
              const o = outcomes[c.id];
              return (
                <tr key={c.id} data-testid={`case-${c.id}`} aria-current={current === c.id ? "step" : undefined}>
                  <td className="mono">{i + 1}</td>
                  <td>
                    {c.label}
                    {c.flag ? (
                      <span className="small muted block">
                        score {c.flag.riskScore}, confidence {c.flag.confidence}
                      </span>
                    ) : null}
                  </td>
                  <td className="num mono">{rupees(c.amountMinor)}</td>
                  <td className="small">{c.why}</td>
                  <td>
                    <StatusPill decision={pillDecision(c.expect)}>{WORD[pillDecision(c.expect)]}</StatusPill>
                  </td>
                  <td data-testid={`case-${c.id}-actual`}>
                    {o ? (
                      <StatusPill decision={pillDecision(o.actual)}>
                        {WORD[pillDecision(o.actual)]}
                        {o.actual === c.expect ? "" : " ≠"}
                      </StatusPill>
                    ) : current === c.id ? (
                      <span className="muted">…</span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="mono small">{o ? o.auditRef.slice(0, 8) : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableWrap>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
    </Panel>
  );
}

function Scenario() {
  const live = useLive();
  const { busy, error, run } = useAction();
  const [flag, setFlag] = useState<FlagResult | null>(null);
  const [transfer, setTransfer] = useState<TransferResult | null>(null);
  const [forge, setForge] = useState<ForgeResult | null>(null);
  const [playing, setPlaying] = useState(false);
  const alive = useRef(true);
  useEffect(
    () => () => {
      alive.current = false;
    },
    [],
  );

  const locked = live.status !== "online" || busy || playing;
  const doFlag = () => run(async () => setFlag(await api.flag(STORY.ifsc, STORY.account)));
  const doTransfer = () =>
    run(async () => {
      const r = await api.transfer({
        receiver_ifsc: STORY.ifsc,
        receiver_account: STORY.account,
        amount_minor: STORY.amountMinor,
      });
      setTransfer(r);
      live.setLastAuditRef(r.audit_ref);
      live.bumpFeed();
    });
  const doForge = () => run(async () => setForge(await api.forge(STORY.ifsc, STORY.account)));
  const doReset = () =>
    run(async () => {
      await api.reset();
      setFlag(null);
      setTransfer(null);
      setForge(null);
      live.bumpFeed();
      return true;
    });

  async function play() {
    const pause = (ms: number) =>
      new Promise((r) => setTimeout(r, prefersReducedMotion() ? 0 : ms));
    setPlaying(true);
    try {
      if ((await doReset()) === null) return;
      for (const step of [doFlag, doTransfer, doForge]) {
        if (!alive.current) return;
        await pause(600);
        if ((await step()) === null) return;
      }
    } finally {
      if (alive.current) setPlaying(false);
    }
  }

  const decision = transfer ? pillDecision(transfer.decision) : null;

  return (
    <div className="grid-2 scenario">
      <Panel
        title="Scenario"
        aside={
          <div className="panel-actions">
            <button type="button" className="btn btn-sm" onClick={doReset} disabled={locked}>
              Reset
            </button>
            <button type="button" className="btn btn-sm btn-primary" onClick={play} disabled={locked}>
              {playing ? "Running…" : "Run scenario"}
            </button>
          </div>
        }
      >
        <table className="steps-table">
          <tbody>
            <tr>
              <td className="num">1</td>
              <td>
                <strong>Bank B flags {STORY.account}</strong>
                <span className="muted">Publishes a signed score, never the account number.</span>
              </td>
              <td className="act">
                <button type="button" className="btn btn-sm" onClick={doFlag} disabled={locked}>
                  Publish
                </button>
              </td>
            </tr>
            <tr>
              <td className="num">2</td>
              <td>
                <strong>Anu sends {rupees(STORY.amountMinor)} from Bank A</strong>
                <span className="muted">Bank A derives the same ID and checks the bus.</span>
              </td>
              <td className="act">
                <button type="button" className="btn btn-sm" onClick={doTransfer} disabled={locked}>
                  Send
                </button>
              </td>
            </tr>
            <tr>
              <td className="num">3</td>
              <td>
                <strong>BANK-EVIL forges a retraction</strong>
                <span className="muted">A score of 0 in Bank B's name, signed with its own key.</span>
              </td>
              <td className="act">
                <button type="button" className="btn btn-sm" onClick={doForge} disabled={locked}>
                  Forge
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
      </Panel>

      <Panel title="Result">
        <dl className="kv" aria-live="polite">
          <dt>Receiver</dt>
          <dd>
            <AccountRef ifsc={STORY.ifsc} account={STORY.account} />
          </dd>
          <dt>Decision</dt>
          <dd data-testid="story-decision">
            {decision ? (
              <>
                <StatusPill decision={decision}>{WORD[decision]}</StatusPill>
                <span className="small muted block">{transfer?.user_message}</span>
              </>
            ) : (
              <span className="muted">Not sent</span>
            )}
          </dd>
          <dt>Audit ref</dt>
          <dd data-testid="story-ref">
            {transfer ? (
              <span className="mono small">{transfer.audit_ref}</span>
            ) : (
              <span className="muted">Issued when the transfer is decided</span>
            )}
          </dd>
          <dt>Signal</dt>
          <dd data-testid="flag-result">
            {flag ? (
              <>
                <span>
                  {flag.publisher}, score <span className="mono">{flag.network.score}</span>
                </span>
                <span className="chips">
                  <HashChip value={flag.risk_id} label="risk_id" />
                  <HashChip value={flag.signature} label="sig" head={6} tail={6} />
                </span>
              </>
            ) : (
              <span className="muted">None published</span>
            )}
          </dd>
          <dt>Forgery</dt>
          <dd data-testid="forge-result">
            {forge ? (
              <>
                <StatusPill decision={forge.result === "rejected" ? "block" : "allow"}>
                  {forge.result === "rejected" ? "Rejected: signature invalid" : forge.result}
                </StatusPill>
                <span className="small muted block">
                  Score on the bus remains <span className="mono">{forge.network.score ?? "none"}</span>
                </span>
              </>
            ) : (
              <span className="muted">Not attempted</span>
            )}
          </dd>
        </dl>
      </Panel>
    </div>
  );
}

function Sandbox() {
  const live = useLive();
  const { busy, error, run } = useAction();
  const ids = { ifsc: useId(), account: useId(), amount: useId(), score: useId(), confidence: useId() };
  const [ifsc, setIfsc] = useState("HDFC0001234");
  const [account, setAccount] = useState("ACC-12345");
  const [amount, setAmount] = useState("2,500");
  const [score, setScore] = useState("92");
  const [confidence, setConfidence] = useState("0.94");
  const [result, setResult] = useState<TransferResult | null>(null);
  const [flagged, setFlagged] = useState<string | null>(null);

  const scoreN = /^\d{1,3}$/.test(score.trim()) ? Number(score) : NaN;
  const confN = /^(0(\.\d+)?|1(\.0+)?)$/.test(confidence.trim()) ? Number(confidence) : NaN;
  const flagOk = scoreN >= 0 && scoreN <= 100 && confN >= 0 && confN <= 1;
  const ifscOk = isValidIfsc(ifsc);
  const amountMinor = parseRupees(amount);
  const valid = ifscOk && account.trim().length > 0 && amountMinor !== null;
  const disabled = live.status !== "online" || busy || !valid;

  const send = (e: FormEvent) => {
    e.preventDefault();
    if (disabled || amountMinor === null) return;
    void run(async () => {
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
    run(async () => {
      const r = await api.flag(ifsc.trim().toUpperCase(), account.trim(), {
        riskScore: scoreN,
        confidence: confN,
      });
      setFlagged(`Flagged by ${r.publisher}, score ${r.network.score} (${r.outcome})`);
    });

  return (
    <Panel title="Sandbox" testId="sandbox">
      <form className="form" onSubmit={send} noValidate>
        <div className="field">
          <label htmlFor={ids.ifsc}>Receiver IFSC</label>
          <input id={ids.ifsc} className="input mono" value={ifsc} onChange={(e) => setIfsc(e.target.value)} aria-invalid={!ifscOk} aria-describedby={ifscOk ? undefined : `${ids.ifsc}-e`} autoComplete="off" spellCheck={false} />
          {!ifscOk ? <p id={`${ids.ifsc}-e`} className="field-error">Format: four letters, 0, six characters.</p> : null}
        </div>
        <div className="field">
          <label htmlFor={ids.account}>Receiver account</label>
          <input id={ids.account} className="input mono" value={account} onChange={(e) => setAccount(e.target.value)} autoComplete="off" spellCheck={false} />
        </div>
        <div className="field">
          <label htmlFor={ids.amount}>Amount (₹)</label>
          <input id={ids.amount} className="input mono" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} aria-invalid={amountMinor === null} aria-describedby={amountMinor === null ? `${ids.amount}-e` : undefined} />
          {amountMinor === null ? <p id={`${ids.amount}-e`} className="field-error">Positive amount, up to two decimals.</p> : null}
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor={ids.score}>Flag score (0–100)</label>
            <input id={ids.score} className="input mono" inputMode="numeric" value={score} onChange={(e) => setScore(e.target.value)} aria-invalid={!(scoreN >= 0 && scoreN <= 100)} />
          </div>
          <div className="field">
            <label htmlFor={ids.confidence}>Confidence (0–1)</label>
            <input id={ids.confidence} className="input mono" inputMode="decimal" value={confidence} onChange={(e) => setConfidence(e.target.value)} aria-invalid={!(confN >= 0 && confN <= 1)} />
          </div>
        </div>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={disabled}>
            Send transfer
          </button>
          <button type="button" className="btn" onClick={flag} disabled={disabled || !flagOk}>
            Flag as Bank B
          </button>
        </div>
      </form>
      <div className="result" aria-live="polite" data-testid="console-result">
        {flagged ? <p className="small">{flagged}.</p> : null}
        {result ? (
          <p className="result-line">
            <StatusPill decision={pillDecision(result.decision)} />
            <span className="small muted">ref</span> <span className="mono small">{result.audit_ref}</span>
          </p>
        ) : null}
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}

function DecisionLog() {
  const live = useLive();
  const [rows, setRows] = useState<FeedRow[]>([]);
  // Load a decision's reference into Investigator access and move there.
  const inspect = (ref: string) => {
    live.setLastAuditRef(ref);
    const panel = document.getElementById("investigator");
    panel?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
    document.getElementById("investigator-key")?.focus({ preventScroll: true });
  };
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
    <Panel title="Decision log" aside={<span className="small muted">no account numbers stored</span>}>
      {rows.length === 0 ? (
        <p className="muted small">No decisions yet.</p>
      ) : (
        <TableWrap label="Decision log">
          <table className="data" data-testid="feed">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Decision</th>
                <th scope="col" className="num">Amount</th>
                <th scope="col">risk_id</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.audit_ref}>
                  <td className="mono">{shortTime(r.decided_at)}</td>
                  <td>
                    <StatusPill decision={pillDecision(r.decision)} />
                  </td>
                  <td className="mono num">{rupees(r.amount_minor)}</td>
                  <td>
                    <HashChip value={r.risk_id || "unavailable"} />
                  </td>
                  <td className="act">
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => inspect(r.audit_ref)}
                      aria-label={`Inspect decision at ${shortTime(r.decided_at)}`}
                    >
                      Inspect
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Panel>
  );
}

function Investigator() {
  const live = useLive();
  const ids = { id: useId(), reason: useId(), ref: useId() };
  const [idEdit, setId] = useState<string | null>(null);
  const investigator = idEdit ?? live.investigatorId ?? "";
  const [key, setKey] = useState("");
  const [reason, setReason] = useState("Review of blocked transfer");
  const [refEdit, setRefEdit] = useState<{ base: string | null; value: string } | null>(null);
  const ref = refEdit && refEdit.base === live.lastAuditRef ? refEdit.value : (live.lastAuditRef ?? "");
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
      const status = err instanceof ApiError ? err.status : -1;
      setError(
        status === 401
          ? "Credentials rejected (401)."
          : status === 404
            ? "No record for that reference."
            : err instanceof ApiError
              ? err.message
              : String(err),
      );
    } finally {
      setBusy(false);
    }
  };
  const disabled =
    live.status !== "online" || busy || !investigator.trim() || !key || !reason.trim() || !ref.trim();

  return (
    <Panel
      title="Investigator access"
      aside={<span className="small muted">encrypted at rest · every read is logged</span>}
      className="investigator"
      id="investigator"
    >
      <p className="small muted intro-line">
        Every decision gets an audit reference. The latest one is filled in automatically; use{" "}
        <strong>Inspect</strong> in the decision log to choose another. The key is printed by the demo
        server at startup.
      </p>
      <form className="form form-row" onSubmit={submit}>
        <div className="field">
          <label htmlFor={ids.id}>Investigator</label>
          <input id={ids.id} className="input mono" value={investigator} onChange={(e) => setId(e.target.value)} autoComplete="username" />
        </div>
        <div className="field">
          <label htmlFor="investigator-key">Key</label>
          <input id="investigator-key" className="input mono" type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="current-password" />
        </div>
        <div className="field">
          <label htmlFor={ids.reason}>Reason</label>
          <input id={ids.reason} className="input" value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <div className="field field-wide">
          <label htmlFor={ids.ref}>Audit reference</label>
          <input id={ids.ref} className="input mono" value={ref} onChange={(e) => setRefEdit({ base: live.lastAuditRef, value: e.target.value })} spellCheck={false} />
        </div>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={disabled}>
            Open record
          </button>
        </div>
      </form>
      <div aria-live="polite" data-testid="audit-result">
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
        {record ? (
          <dl className="kv kv-cols">
            <dt>Decision</dt>
            <dd>
              <StatusPill decision={pillDecision(record.decision)} />
            </dd>
            <dt>Network score</dt>
            <dd className="mono" data-testid="audit-score">
              {record.risk_score ?? "none"}
            </dd>
            <dt>Flagged by</dt>
            <dd className="mono">{record.contributing_banks.join(", ") || "none"}</dd>
            <dt>Thresholds</dt>
            <dd className="mono" data-testid="audit-policy">
              step-up ≥ {record.policy.step_up_at} · block ≥ {record.policy.block_at}
            </dd>
            <dt>Reason codes</dt>
            <dd className="mono">{record.reason_codes.join(", ") || "none"}</dd>
            <dt>risk_id</dt>
            <dd>
              <HashChip value={record.risk_id || "unavailable"} />
            </dd>
          </dl>
        ) : null}
      </div>
    </Panel>
  );
}
