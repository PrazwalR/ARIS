import { useEffect, useState } from "react";
import { GlassCard, MetricTile, StatusPill, ToggleChip } from "@aris/components/aris.esm.js";
import { Section, TableWrap } from "../components/common";
import type { MetricsData } from "../lib/data";
import { fixed, scientific } from "../lib/format";
import { prefersReducedMotion, useReveal } from "../lib/ui";

const BANKS = ["BANK-A", "BANK-B", "BANK-C", "BANK-D", "BANK-E"];
const ROUND_MS = 900;

export function Federated({ data }: { data: MetricsData }) {
  const [dp, setDp] = useState(false);
  const [secAgg, setSecAgg] = useState(false);
  const [krum, setKrum] = useState(false);
  const ds = data.datasets.synthetic;

  return (
    <Section
      id="federated"
      eyebrow="Federated learning"
      title="Five banks train one model. Only weights leave each bank."
      intro="Each bank trains on its own transactions; an aggregator combines the weight updates with FedAvg. Turn on each protection to see what it does, measured on this build."
      band="tint"
    >
      <div className="toggle-row" role="group" aria-label="Training protections">
        <ToggleChip label="DP-SGD" hint="differential privacy" pressed={dp} onChange={setDp} />
        <ToggleChip
          label="Secure aggregation"
          hint="pairwise masks"
          pressed={secAgg}
          onChange={setSecAgg}
        />
        <ToggleChip label="Krum" hint="Byzantine-robust" pressed={krum} onChange={setKrum} />
      </div>

      <div className="fl-grid">
        <GlassCard lift={false} className="fl-diagram">
          <RoundCounter rounds={ds.rounds} />
          <ul className="fl-banks" aria-label="Participating banks">
            {BANKS.map((b) => (
              <li key={b} className="fl-bank">
                <span className="fl-node">{b}</span>
                <span className="mono-s muted">{secAgg ? "masked update" : "weight update"}</span>
              </li>
            ))}
            {krum ? (
              <li className="fl-bank fl-evil" data-testid="evil-bank">
                <span className="fl-node">BANK-EVIL</span>
                <StatusPill decision="block">Update rejected</StatusPill>
              </li>
            ) : null}
          </ul>
          <div className="fl-aggregator">
            <span className="flow-title">Aggregator</span>
            <span className="mono-s muted">
              {krum
                ? "Krum: keeps the update closest to its peers"
                : secAgg
                  ? "sees only the masked sum"
                  : "FedAvg: weighted mean of updates"}
            </span>
          </div>
        </GlassCard>

        <div className="fl-panels">
          <GlassCard
            lift={false}
            eyebrow="Banks together vs each bank alone"
            title={`Global AUC ${fixed(ds.global.metrics.auc)}`}
          >
            <p className="body-s muted">
              Mean of the five local-only models: <b className="mono">{fixed(ds.mean_local.auc)}</b>.
              Synthetic data where each bank's fraud depends on a different feature, so no bank can
              learn the others' patterns alone.
            </p>
          </GlassCard>

          {dp ? <PrivacyPanel data={data} /> : null}
          {secAgg ? (
            <GlassCard lift={false} eyebrow="Secure aggregation" title="Masks cancel exactly">
              <p className="body-s muted" data-testid="secagg-panel">
                Each pair of banks adds and subtracts the same random mask, so the aggregator sees
                no individual update. The masked sum differs from plain FedAvg by at most{" "}
                <b className="mono">{scientific(data.secure_aggregation.max_abs_error_vs_plain_fedavg)}</b>{" "}
                (float32 rounding) across {data.secure_aggregation.banks} banks.
              </p>
            </GlassCard>
          ) : null}
          {krum ? <RobustnessPanel data={data} /> : null}
          {secAgg && krum ? (
            <p className="note-line body-s" role="note" data-testid="combo-note">
              Not supported together: masking hides the individual updates Krum needs to compare.
              Training rejects this combination rather than silently picking one.
            </p>
          ) : null}
        </div>
      </div>
    </Section>
  );
}

function RoundCounter({ rounds }: { rounds: MetricsData["datasets"]["synthetic"]["rounds"] }) {
  const [ref, shown] = useReveal<HTMLDivElement>();
  const [i, setI] = useState(() => (prefersReducedMotion() ? rounds.length - 1 : 0));
  useEffect(() => {
    if (!shown || prefersReducedMotion() || i >= rounds.length - 1) return;
    const id = setTimeout(() => setI(i + 1), ROUND_MS);
    return () => clearTimeout(id);
  }, [shown, i, rounds.length]);
  const r = rounds[i];
  return (
    <div ref={ref} className="round-counter" data-testid="round-counter">
      <span className="mono">
        Round {r.round} / {rounds.length}
      </span>
      <span className="mono-s muted">global AUC {fixed(r.auc, 4)}</span>
    </div>
  );
}

function PrivacyPanel({ data }: { data: MetricsData }) {
  const rows = data.privacy.rows.filter((r) => r.epsilon !== null);
  const baseline = data.privacy.rows.find((r) => r.epsilon === null);
  return (
    <GlassCard lift={false} eyebrow="DP-SGD" title="Privacy budget against accuracy">
      <p className="body-s muted">
        Per-example gradient clipping plus Gaussian noise. ε is a conservative upper bound at
        δ = {data.privacy.delta}. Without DP the global AUC is{" "}
        <b className="mono">{baseline ? fixed(baseline.global_auc) : "n/a"}</b>.
      </p>
      <TableWrap label="Privacy budget against accuracy">
        <table className="data-table" data-testid="privacy-table">
          <caption className="sr-only">Noise multiplier, epsilon and global AUC</caption>
          <thead>
            <tr>
              <th scope="col">Noise σ</th>
              <th scope="col">ε (upper bound)</th>
              <th scope="col">Global AUC</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.noise_multiplier}>
                <td className="mono">{r.noise_multiplier}</td>
                <td className="mono">{r.epsilon}</td>
                <td className="mono">{fixed(r.global_auc)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </GlassCard>
  );
}

function RobustnessPanel({ data }: { data: MetricsData }) {
  const r = data.robustness;
  return (
    <GlassCard lift={false} eyebrow="Krum" title="One malicious bank, every round">
      <p className="body-s muted">
        Attack: {r.attack}, for {r.rounds} rounds alongside {r.honest_banks} honest banks.
      </p>
      <div className="tile-row" data-testid="robustness-tiles">
        <MetricTile value={fixed(r.fedavg_auc)} label="FedAvg AUC" context="dragged toward random" />
        <MetricTile value={fixed(r.krum_auc)} label="Krum AUC" context="keeps learning" />
      </div>
    </GlassCard>
  );
}
