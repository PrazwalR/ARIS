import { useState } from "react";
import { GlassCard, MetricTile, RocChart } from "@aris/components/aris.esm.js";
import { Section, TableWrap, Tabs } from "../components/common";
import type { DatasetKey, MetricsData, ModelMetrics } from "../lib/data";
import { fixed, grouped, percent } from "../lib/format";

export function Metrics({ data }: { data: MetricsData }) {
  const [key, setKey] = useState<DatasetKey>("ulb");
  const ds = data.datasets[key];
  const g = ds.global.metrics;
  const m = ds.mean_local;
  const panelId = "metrics-panel";

  // Local banks are labelled once, as a group: their curves overlap too
  // closely for five direct labels to sit next to anything. The table below
  // carries each bank's own numbers.
  const series = [
    ...ds.local.map((l, i) => ({
      label: i === 0 ? "Each bank alone" : undefined,
      points: l.roc,
      labelY: 0.9,
    })),
    { label: "Banks together", points: ds.global.roc, emphasis: true, labelY: 0.98 },
  ];

  return (
    <Section
      id="metrics"
      eyebrow="Metrics"
      title="Measured on held-out data no model trained on"
      intro="Every number here was produced by this build's own training run and is reproducible from the repository."
    >
      <Tabs
        label="Dataset"
        panelId={panelId}
        value={key}
        onChange={setKey}
        options={[
          { key: "ulb", label: data.datasets.ulb.label },
          { key: "synthetic", label: data.datasets.synthetic.label },
        ]}
      />
      <div id={panelId} role="tabpanel" aria-labelledby={`${panelId}-tab-${key}`} className="metrics-panel">
        <p className="body-s muted dataset-note">
          {ds.note} Holdout: <b className="mono">{grouped(ds.holdout.rows)}</b> rows,{" "}
          <b className="mono">{grouped(ds.holdout.fraud)}</b> fraud (
          {percent(ds.holdout.fraud / ds.holdout.rows, 2)}).
        </p>
        {/* key: remount on dataset switch so tiles count up to the new value */}
        <div className="tile-grid" key={key} data-testid="metric-tiles">
          <MetricTile
            value={fixed(g.auc)}
            label="Global AUC"
            context={<>vs <b>{fixed(m.auc)}</b> mean local</>}
          />
          <MetricTile
            value={fixed(g.pr_auc)}
            label="Global PR-AUC"
            context={<>vs <b>{fixed(m.pr_auc)}</b> mean local</>}
          />
          <MetricTile
            value={fixed(g.recall_at_0_5)}
            label="Recall at 0.5"
            context={<>precision <b>{fixed(g.precision_at_0_5)}</b></>}
          />
        </div>
        <p className="body-s honesty" data-testid="honesty-note">
          AUC and PR-AUC lead. Accuracy is secondary on imbalanced fraud data: predicting "not
          fraud" for every row would already score{" "}
          <b className="mono">{percent(1 - ds.holdout.fraud / ds.holdout.rows)}</b> here.
        </p>

        <div className="metrics-grid">
          <GlassCard lift={false} className="chart-card">
            <RocChart
              key={key}
              title={`ROC · ${ds.label}`}
              ariaLabel={`ROC curves for ${ds.label}: global FedAvg AUC ${fixed(g.auc)}; five local-only banks`}
              series={series}
              width={420}
              height={340}
            />
          </GlassCard>
          <GlassCard lift={false} className="table-card">
            <TableWrap label="Per-bank results">
              <table className="data-table" data-testid="bank-table">
                <caption className="body-s muted caption-left">
                  Each bank alone vs banks together, threshold 0.5 for precision, recall and accuracy.
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Model</th>
                    <th scope="col">Train rows</th>
                    <th scope="col">AUC</th>
                    <th scope="col">PR-AUC</th>
                    <th scope="col">Precision</th>
                    <th scope="col">Recall</th>
                    <th scope="col">Accuracy</th>
                  </tr>
                </thead>
                <tbody>
                  {ds.local.map((l) => (
                    <Row key={l.bank_id} name={`${l.bank_id} (local)`} rows={grouped(l.train_rows)} m={l.metrics} />
                  ))}
                  <Row name="Mean of local models" rows="" m={m} muted />
                  <Row
                    name="Global FedAvg"
                    rows={grouped(ds.local.reduce((a, l) => a + l.train_rows, 0))}
                    m={g}
                    strong
                  />
                </tbody>
              </table>
            </TableWrap>
          </GlassCard>
        </div>
      </div>
    </Section>
  );
}

function Row(props: { name: string; rows: string; m: ModelMetrics; strong?: boolean; muted?: boolean }) {
  const cls = props.strong ? "row-strong" : props.muted ? "row-muted" : undefined;
  return (
    <tr className={cls}>
      <th scope="row">{props.name}</th>
      <td className="mono">{props.rows}</td>
      <td className="mono">{fixed(props.m.auc)}</td>
      <td className="mono">{fixed(props.m.pr_auc)}</td>
      <td className="mono">{fixed(props.m.precision_at_0_5)}</td>
      <td className="mono">{fixed(props.m.recall_at_0_5)}</td>
      <td className="mono">{fixed(props.m.accuracy_at_0_5)}</td>
    </tr>
  );
}
