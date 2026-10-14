import { useEffect, useRef, useState } from "react";
import { GlassCard, HashChip, StatusPill } from "@aris/components/aris.esm.js";
import { Section } from "../components/common";
import { api } from "../lib/api";
import { useLive } from "../lib/liveContext";
import { POLICY, STORY } from "../lib/story";

const NODES = [
  { key: "publish", title: "Bank B", sub: "publishes a signed signal" },
  { key: "verify", title: "Shared risk-signal bus", sub: "verifies every signature" },
  { key: "derive", title: "Bank A · BankBot", sub: "derives the same risk_id" },
  { key: "decide", title: "Policy", sub: "allow · step-up · block" },
  { key: "evidence", title: "Encrypted audit log", sub: "named investigators only" },
] as const;

export function HowItWorks() {
  const { status } = useLive();
  const [riskId, setRiskId] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const stepRefs = useRef<(HTMLLIElement | null)[]>([]);

  useEffect(() => {
    if (status !== "online") return;
    api
      .riskId(STORY.ifsc, STORY.account)
      .then((r) => setRiskId(r.risk_id))
      .catch(() => setRiskId(null));
  }, [status]);

  useEffect(() => {
    if (typeof IntersectionObserver !== "function") return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) setActive(Number((e.target as HTMLElement).dataset.index));
        }
      },
      { rootMargin: "-45% 0px -45% 0px" },
    );
    stepRefs.current.forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, []);

  const steps = [
    {
      eyebrow: "Step 1 · Bank B",
      title: "Publish what you know, not who it is",
      body: "Bank B has confirmed fraud into an account. It publishes a risk score, reason codes and a pseudonymous risk_id, signed with its Ed25519 key. The account number never leaves Bank B.",
      extra: riskId ? (
        <HashChip value={riskId} label="risk_id for ACC-999" />
      ) : (
        <p className="mono-s muted">risk_id = HMAC-SHA256(epoch key, len‖IFSC‖len‖account)</p>
      ),
    },
    {
      eyebrow: "Step 2 · The bus",
      title: "Every signal is verified against its publisher's key",
      body: "Signals live on a Kafka topic, checked against the consortium keyring when published and again when consumed. A signal claiming to be from Bank B but signed by anyone else is rejected.",
      extra: <StatusPill decision="block">Forged signal: signature invalid</StatusPill>,
    },
    {
      eyebrow: "Step 3 · Bank A",
      title: "Derive the same id, independently",
      body: "When Anu pays ACC-999, Bank A's BankBot derives the same risk_id under the same scheme: HMAC with daily epoch keys by default, or with the key held in an HSM, or via the consortium's blind-signature authority so no bank can compute ids offline.",
      extra: <p className="mono-s muted">today's epoch first, then yesterday's</p>,
    },
    {
      eyebrow: "Step 4 · Policy",
      title: "Decide before any money moves",
      body: `Score ${POLICY.blockAt} or above blocks; ${POLICY.stepUpAt} or above asks for step-up verification. An unreachable bus never resolves to allow. The customer sees only the decision, never the score or the flagging bank.`,
      extra: (
        <div className="pill-row">
          <StatusPill decision="allow" />
          <StatusPill decision="stepup">Step-up ≥ {POLICY.stepUpAt}</StatusPill>
          <StatusPill decision="block">Block ≥ {POLICY.blockAt}</StatusPill>
        </div>
      ),
    },
    {
      eyebrow: "Step 5 · Evidence",
      title: "Evidence for investigators, not for the transfer form",
      body: "The full record (score, contributing banks, thresholds in force) is encrypted at rest with AES-256-GCM. Reading one needs a named investigator and a stated reason, and every read is logged.",
      extra: <p className="mono-s muted">GET /api/audit/&#123;ref&#125; · X-Investigator-Id · reason</p>,
    },
  ];

  return (
    <Section
      id="how"
      eyebrow="How it works"
      title="One flagged account, five steps, no shared records"
      intro="Follow Anu's transfer from Bank B's first report to Bank A's decision."
      band="alt"
    >
      <div className="how-grid">
        <div className="how-visual" aria-hidden="true">
          <ol className="flow">
            {NODES.map((n, i) => (
              <li key={n.key} className="flow-node" data-active={i === active} data-past={i < active}>
                <span className="flow-dot" />
                <span>
                  <span className="flow-title">{n.title}</span>
                  <span className="flow-sub">{n.sub}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>
        <ol className="how-steps">
          {steps.map((s, i) => (
            <li
              key={s.title}
              data-index={i}
              ref={(el) => {
                stepRefs.current[i] = el;
              }}
            >
              <GlassCard eyebrow={s.eyebrow} title={s.title} body={s.body}>
                <div className="step-extra">{s.extra}</div>
              </GlassCard>
            </li>
          ))}
        </ol>
      </div>
    </Section>
  );
}
