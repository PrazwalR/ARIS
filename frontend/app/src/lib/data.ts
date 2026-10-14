import { useEffect, useState } from "react";

/** Shape of public/data/metrics.json, written by scripts/export_frontend_data.py.
 * Every number the page shows comes from this file or from the live API. */

export interface ModelMetrics {
  auc: number;
  pr_auc: number;
  precision_at_0_5: number;
  recall_at_0_5: number;
  accuracy_at_0_5: number;
}

export type RocPoints = [number, number][];

export interface LocalModel {
  bank_id: string;
  train_rows: number;
  train_fraud: number;
  metrics: ModelMetrics;
  roc: RocPoints;
}

export interface DatasetResult {
  label: string;
  note: string;
  partition: string;
  config: { banks: number; rounds: number; local_epochs: number; max_rows: number | null };
  holdout: { rows: number; fraud: number };
  local: LocalModel[];
  mean_local: ModelMetrics;
  global: { metrics: ModelMetrics; roc: RocPoints };
  rounds: { round: number; auc: number; pr_auc: number }[];
}

export interface PrivacyRow {
  noise_multiplier: number | null;
  epsilon: number | null;
  global_auc: number;
}

export interface LoadResult {
  publishers: number;
  signals: number;
  throughput_per_s: number;
  lost_updates: number;
  lookup_p99_ms: number;
}

export interface MetricsData {
  generated_at: string;
  commit: string | null;
  datasets: { ulb: DatasetResult; synthetic: DatasetResult };
  privacy: { dataset: string; delta: number; mean_local_auc: number; rows: PrivacyRow[] };
  secure_aggregation: { banks: number; max_abs_error_vs_plain_fedavg: number };
  robustness: {
    attack: string;
    rounds: number;
    honest_banks: number;
    fedavg_auc: number;
    krum_auc: number;
  };
  load: { memory: LoadResult; kafka: LoadResult | null; kafka_unavailable?: string };
  checks?: { ruff: boolean; mypy_strict: boolean };
  tests?: {
    command: string;
    summary: string;
    passed: number;
    failed: number;
    skipped: number;
    duration_s: number;
    exit_code: number;
  };
}

export type DatasetKey = keyof MetricsData["datasets"];

export type Loadable<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "error"; message: string };

export function useMetrics(url = "/data/metrics.json"): Loadable<MetricsData> {
  const [result, setResult] = useState<Loadable<MetricsData>>({ state: "loading" });
  useEffect(() => {
    let alive = true;
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`${url} returned ${r.status}`);
        return r.json() as Promise<MetricsData>;
      })
      .then((data) => alive && setResult({ state: "ready", data }))
      .catch((e: unknown) =>
        alive && setResult({ state: "error", message: e instanceof Error ? e.message : String(e) }),
      );
    return () => {
      alive = false;
    };
  }, [url]);
  return result;
}
