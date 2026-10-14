import type { MetricsData, DatasetResult } from "../lib/data";

const m = (auc: number) => ({
  auc,
  pr_auc: auc - 0.1,
  precision_at_0_5: 0.3,
  recall_at_0_5: 0.9,
  accuracy_at_0_5: 0.98,
});
const roc: [number, number][] = [
  [0, 0],
  [0.1, 0.8],
  [1, 1],
];

function dataset(label: string, globalAuc: number): DatasetResult {
  return {
    label,
    note: "Fixture.",
    partition: "temporal",
    config: { banks: 5, rounds: 2, local_epochs: 1, max_rows: null },
    holdout: { rows: 1000, fraud: 10 },
    local: ["BANK-A", "BANK-B", "BANK-C", "BANK-D", "BANK-E"].map((b, i) => ({
      bank_id: b,
      train_rows: 100 + i,
      train_fraud: 2,
      metrics: m(0.9 + i / 100),
      roc,
    })),
    mean_local: m(0.92),
    global: { metrics: m(globalAuc), roc },
    rounds: [
      { round: 1, auc: 0.8, pr_auc: 0.5 },
      { round: 2, auc: globalAuc, pr_auc: 0.6 },
    ],
  };
}

export const fixture: MetricsData = {
  generated_at: "2026-10-15T00:00:00+00:00",
  commit: "abc1234",
  datasets: { ulb: dataset("ULB fixture", 0.987), synthetic: dataset("Synthetic fixture", 0.655) },
  privacy: {
    dataset: "synthetic",
    delta: 1e-5,
    mean_local_auc: 0.572,
    rows: [
      { noise_multiplier: null, epsilon: null, global_auc: 0.655 },
      { noise_multiplier: 1, epsilon: 118.29, global_auc: 0.634 },
    ],
  },
  secure_aggregation: { banks: 5, max_abs_error_vs_plain_fedavg: 5.96e-8 },
  robustness: { attack: "fixture attack", rounds: 6, honest_banks: 5, fedavg_auc: 0.5017, krum_auc: 0.5876 },
  load: {
    memory: { publishers: 20, signals: 4000, throughput_per_s: 3393.8, lost_updates: 0, lookup_p99_ms: 0 },
    kafka: null,
  },
  checks: { ruff: true, mypy_strict: false },
  tests: { command: "pytest", summary: "926 passed", passed: 926, failed: 0, skipped: 0, duration_s: 80, exit_code: 0 },
};
