import { useState } from "react";
import { RocChart } from "@aris/components/aris.esm.js";
import { TableWrap, Tabs } from "../components/common";
import { PageHead, Panel } from "../components/Layout";
import type { DatasetKey, MetricsData, ModelMetrics } from "../lib/data";
import { fixed, grouped, percent, scientific } from "../lib/format";

export function Model({ data }: { data: MetricsData }) {
  const [key, setKey] = useState<DatasetKey>("ulb");
  const ds = data.datasets[key];
  const g = ds.global.metrics;
  const m = ds.mean_local;
  const panelId = "dataset-panel";
  const fraudRate = ds.holdout.fraud / ds.holdout.rows;

  const series = [
    ...ds.local.map((l, i) => ({
      label: i === 0 ? "Single bank" : undefined,
      points: l.roc,
      labelY: 0.9,
    })),
    { label: "Federated", points: ds.global.roc, emphasis: true, labelY: 0.98 },
  ];

  return (
    <div className="container">
      <PageHead
        title="Model results"
        lede="Five banks train one model with federated averaging. Evaluated on held-out data no model trained on."
      >
        <Tabs
          label="Dataset"
          panelId={panelId}
          value={key}
          onChange={setKey}
          options={[
            { key: "ulb", label: "ULB card fraud" },
            { key: "synthetic", label: "Synthetic" },
          ]}
        />
      </PageHead>

      <div id={panelId} role="tabpanel" aria-labelledby={`${panelId}-tab-${key}`}>
        <dl className="figures" data-testid="metric-tiles">
          <Figure label="AUC" value={g.auc} vs={m.auc} />
          <Figure label="PR-AUC" value={g.pr_auc} vs={m.pr_auc} />
          <Figure label="Recall at 0.5" value={g.recall_at_0_5} vs={m.recall_at_0_5} />
          <Figure label="Precision at 0.5" value={g.precision_at_0_5} vs={m.precision_at_0_5} />
        </dl>

        <div className="grid-2 grid-chart">
          <Panel title="ROC, held-out set">
            <RocChart
              key={key}
              ariaLabel={`ROC curves: federated model AUC ${fixed(g.auc)}; five single-bank models`}
              series={series}
              width={420}
              height={330}
            />
          </Panel>
          <Panel title="Method">
            <dl className="kv">
              <dt>Data</dt>
              <dd>{ds.label}</dd>
              <dt>Split</dt>
              <dd>
                {ds.partition === "temporal"
                  ? "Banks by time window; holdout is the latest 20%"
                  : "Each bank's fraud on a different feature; pooled holdout"}
              </dd>
              <dt>Holdout</dt>
              <dd className="mono">
                {grouped(ds.holdout.rows)} rows · {grouped(ds.holdout.fraud)} fraud ({percent(fraudRate, 2)})
              </dd>
              <dt>Training</dt>
              <dd className="mono">
                {ds.config.banks} banks · {ds.config.rounds} rounds × {ds.config.local_epochs} epochs
              </dd>
            </dl>
            <p className="note" data-testid="honesty-note">
              AUC and PR-AUC lead. Predicting "not fraud" for every row would score{" "}
              <span className="mono">{percent(1 - fraudRate)}</span> accuracy here, so accuracy and
              fixed-threshold precision are secondary.
            </p>
          </Panel>
        </div>

        <Panel title="Per bank">
          <TableWrap label="Per-bank results">
            <table className="data" data-testid="bank-table">
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  <th scope="col" className="num">Train rows</th>
                  <th scope="col" className="num">AUC</th>
                  <th scope="col" className="num">PR-AUC</th>
                  <th scope="col" className="num">Precision</th>
                  <th scope="col" className="num">Recall</th>
                  <th scope="col" className="num">Accuracy</th>
                </tr>
              </thead>
              <tbody>
                {ds.local.map((l) => (
                  <Row key={l.bank_id} name={`${l.bank_id}, alone`} rows={grouped(l.train_rows)} m={l.metrics} />
                ))}
                <Row name="Mean, single bank" rows="" m={m} muted />
                <Row
                  name="Federated"
                  rows={grouped(ds.local.reduce((a, l) => a + l.train_rows, 0))}
                  m={g}
                  strong
                />
              </tbody>
            </table>
          </TableWrap>
          <p className="small muted">Precision, recall and accuracy at a 0.5 score threshold.</p>
        </Panel>
      </div>

      <h2 className="section-title">Training</h2>
      <div className="grid-2">
        <Panel title="Rounds" aside={<span className="small muted">synthetic, federated AUC</span>}>
          <table className="data compact" data-testid="rounds-table">
            <thead>
              <tr>
                <th scope="col">Round</th>
                <th scope="col" className="num">AUC</th>
                <th scope="col" className="bar-col">
                  <span className="sr-only">Progress</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.datasets.synthetic.rounds.map((r) => (
                <tr key={r.round}>
                  <td className="mono">{r.round}</td>
                  <td className="mono num">{fixed(r.auc, 4)}</td>
                  <td className="bar-col">
                    <span className="bar" style={{ width: `${Math.max(0, (r.auc - 0.5) * 200)}%` }} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <Panel title="Protections" testId="protections">
          <table className="data compact">
            <thead>
              <tr>
                <th scope="col">Protection</th>
                <th scope="col">Measured</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">Robust aggregation (Krum)</th>
                <td data-testid="robustness">
                  Under a malicious bank: FedAvg <span className="mono">{fixed(data.robustness.fedavg_auc)}</span>
                  , Krum <span className="mono">{fixed(data.robustness.krum_auc)}</span> AUC
                </td>
              </tr>
              <tr>
                <th scope="row">Secure aggregation</th>
                <td>
                  Masks cancel to within{" "}
                  <span className="mono">{scientific(data.secure_aggregation.max_abs_error_vs_plain_fedavg)}</span>{" "}
                  of plain FedAvg
                </td>
              </tr>
              <tr>
                <th scope="row">Differential privacy</th>
                <td>See budget table</td>
              </tr>
            </tbody>
          </table>
          <h3 className="sub">DP-SGD budget (synthetic, δ = {data.privacy.delta})</h3>
          <TableWrap label="Privacy budget against accuracy">
            <table className="data compact" data-testid="privacy-table">
              <thead>
                <tr>
                  <th scope="col" className="num">Noise <span className="nocase">σ</span></th>
                  <th scope="col" className="num"><span className="nocase">ε</span> (upper bound)</th>
                  <th scope="col" className="num">AUC</th>
                </tr>
              </thead>
              <tbody>
                {data.privacy.rows.map((r) => (
                  <tr key={String(r.noise_multiplier)}>
                    <td className="mono num">{r.noise_multiplier ?? "no DP"}</td>
                    <td className="mono num">{r.epsilon ?? "—"}</td>
                    <td className="mono num">{fixed(r.global_auc)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Panel>
      </div>
    </div>
  );
}

function Figure({ label, value, vs }: { label: string; value: number; vs: number }) {
  const delta = value - vs;
  return (
    <div>
      <dt>{label}</dt>
      <dd>{fixed(value)}</dd>
      <dd className="fig-note">
        <span className="mono">{fixed(vs)}</span> single bank{" "}
        <span className={delta >= 0 ? "delta-up" : "delta-down"}>
          ({delta >= 0 ? "+" : "−"}
          {fixed(Math.abs(delta))})
        </span>
      </dd>
    </div>
  );
}

function Row(props: { name: string; rows: string; m: ModelMetrics; strong?: boolean; muted?: boolean }) {
  return (
    <tr className={props.strong ? "row-strong" : props.muted ? "row-muted" : undefined}>
      <th scope="row">{props.name}</th>
      <td className="mono num">{props.rows}</td>
      <td className="mono num">{fixed(props.m.auc)}</td>
      <td className="mono num">{fixed(props.m.pr_auc)}</td>
      <td className="mono num">{fixed(props.m.precision_at_0_5)}</td>
      <td className="mono num">{fixed(props.m.recall_at_0_5)}</td>
      <td className="mono num">{fixed(props.m.accuracy_at_0_5)}</td>
    </tr>
  );
}
