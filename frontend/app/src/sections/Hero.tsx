import { useEffect, useRef, useState } from "react";
import {
  Button,
  GlassCard,
  HashChip,
  SkyBackground,
  StatusPill,
  ToggleChip,
} from "@aris/components/aris.esm.js";
import { AccountRef, OfflineNotice } from "../components/common";
import { api, ApiError, pillDecision } from "../lib/api";
import type { FlagResult, ForgeResult, TransferResult } from "../lib/api";
import { rupees } from "../lib/format";
import { useLive } from "../lib/liveContext";
import { STORY } from "../lib/story";
import { prefersReducedMotion } from "../lib/ui";

type Step = "flag" | "transfer" | "forge";

export function Hero() {
  return (
    <SkyBackground mesh minHeight={640} className="hero">
      <div className="container hero-grid">
        <div className="hero-copy ar-scrim">
          <p className="eyebrow">Privacy-preserving fraud intelligence</p>
          <h1 className="display-xl">
            Banks that warn each other, without sharing a single customer record.
          </h1>
          <p className="body-l hero-lede">
            Banks exchange signed, pseudonymous risk signals and jointly train a fraud model. No
            raw transactions, account numbers or labels ever leave a bank.
          </p>
          <div className="cta-row">
            <a className="ar-btn ar-btn-primary" href="#how">
              See how it works
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M5 12h14" />
                <path d="m13 6 6 6-6 6" />
              </svg>
            </a>
            <a className="ar-btn ar-btn-secondary" href="#metrics">
              View the metrics
            </a>
          </div>
        </div>
        <StoryCard />
      </div>
    </SkyBackground>
  );
}

function StoryCard() {
  const live = useLive();
  const [flag, setFlag] = useState<FlagResult | null>(null);
  const [transfer, setTransfer] = useState<TransferResult | null>(null);
  const [forge, setForge] = useState<ForgeResult | null>(null);
  const [busy, setBusy] = useState<Step | "reset" | null>(null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cancelled = useRef(false);
  useEffect(() => () => void (cancelled.current = true), []);

  const offline = live.status !== "online";

  async function run<T>(step: Step | "reset", fn: () => Promise<T>): Promise<T | null> {
    setBusy(step);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      if (e instanceof ApiError && e.status === 0) live.recheck();
      return null;
    } finally {
      if (!cancelled.current) setBusy(null);
    }
  }

  const doFlag = () =>
    run("flag", async () => {
      const r = await api.flag(STORY.ifsc, STORY.account);
      setFlag(r);
      return r;
    });
  const doTransfer = () =>
    run("transfer", async () => {
      const r = await api.transfer({
        receiver_ifsc: STORY.ifsc,
        receiver_account: STORY.account,
        amount_minor: STORY.amountMinor,
      });
      setTransfer(r);
      live.setLastAuditRef(r.audit_ref);
      live.bumpFeed();
      return r;
    });
  const doForge = () =>
    run("forge", async () => {
      const r = await api.forge(STORY.ifsc, STORY.account);
      setForge(r);
      return r;
    });
  const doReset = () =>
    run("reset", async () => {
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
        if (cancelled.current) return;
        await pause(700);
        if ((await step()) === null) return;
      }
    } finally {
      if (!cancelled.current) setPlaying(false);
    }
  }

  const locked = offline || busy !== null || playing;

  const decision = transfer ? pillDecision(transfer.decision) : null;
  const decisionWord = { allow: "Allowed", stepup: "Step-up", block: "Blocked" } as const;

  return (
    <GlassCard className="story-card" lift={false}>
      <div className="story-head">
        <p className="ar-card-eyebrow">Live · the real BankBot over HTTP</p>
        <ToggleChip
          label="Privacy lens"
          hint="what the network sees"
          pressed={live.lens}
          onChange={live.setLens}
        />
      </div>
      <h2 className="h2 story-title">Anu sends {rupees(STORY.amountMinor)}</h2>
      <p className="body-s story-to">
        to <AccountRef ifsc={STORY.ifsc} account={STORY.account} />
      </p>

      <div className="story-decision" aria-live="polite" data-testid="story-decision">
        {decision ? (
          <>
            <StatusPill decision={decision} size="lg">
              {decisionWord[decision]}
            </StatusPill>
            <p className="body-s">{transfer?.user_message}</p>
          </>
        ) : (
          <p className="body-s muted">No decision yet. Bank A checks the network before money moves.</p>
        )}
      </div>

      <OfflineNotice />

      <ol className="story-steps">
        <li data-done={!!flag}>
          <div>
            <p className="step-title">Bank B flags {STORY.account}</p>
            <p className="body-s muted">
              Bank B saw the fraud first. It publishes a signed signal: a pseudonymous risk_id and a
              score, never the account number.
            </p>
            {flag ? (
              <div className="step-result" data-testid="flag-result">
                <span className="body-s">
                  Signed by <strong>{flag.publisher}</strong>, score{" "}
                  <span className="mono">{flag.network.score}</span>
                </span>
                <HashChip value={flag.risk_id} label="risk_id" />
                <HashChip value={flag.signature} label="Ed25519" head={6} tail={6} />
              </div>
            ) : null}
          </div>
          <Button variant="secondary" size="sm" onClick={doFlag} disabled={locked}>
            {flag ? "Published" : "Publish flag"}
          </Button>
        </li>
        <li data-done={!!transfer}>
          <div>
            <p className="step-title">Anu sends {rupees(STORY.amountMinor)}</p>
            <p className="body-s muted">
              Bank A has never seen this account. BankBot derives the same risk_id and asks the
              network.
            </p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={doTransfer}
            disabled={locked}
          >
            Send transfer
          </Button>
        </li>
        <li data-done={!!forge}>
          <div>
            <p className="step-title">A hostile bank forges a retraction</p>
            <p className="body-s muted">
              BANK-EVIL, a registered member, signs a "score 0" signal in Bank B's name with its own
              key.
            </p>
            {forge ? (
              <div className="step-result" data-testid="forge-result">
                <StatusPill decision={forge.result === "rejected" ? "block" : "allow"}>
                  {forge.result === "rejected" ? "Rejected: signature invalid" : forge.result}
                </StatusPill>
                <span className="body-s">
                  Network score still <span className="mono">{forge.network.score ?? "none"}</span>
                  {forge.network.contributing_banks.length
                    ? `, flagged by ${forge.network.contributing_banks.join(", ")}`
                    : ""}
                </span>
              </div>
            ) : null}
          </div>
          <Button variant="secondary" size="sm" onClick={doForge} disabled={locked}>
            Forge retraction
          </Button>
        </li>
      </ol>

      {error ? (
        <p className="body-s error-text" role="alert">
          {error}
        </p>
      ) : null}

      <div className="story-actions">
        <Button
          variant="secondary"
          iconEnd="arrowRight"
          onClick={play}
          disabled={locked}
        >
          {playing ? "Playing…" : "Play the whole story"}
        </Button>
        <Button variant="ghost" size="sm" onClick={doReset} disabled={locked}>
          Reset
        </Button>
      </div>
    </GlassCard>
  );
}
