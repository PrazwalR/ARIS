# Where every number comes from

Every figure used in the review, the slides and the website, with the file that
holds it, the key inside that file, and the command that regenerates it. Run
commands from the repo root with the venv active (`source /tmp/aris_ci_sim/bin/activate`).
Kafka rows need `docker compose up -d`.

## 1. Website figures (single run, seed 42)

File: `frontend/app/public/data/metrics.json`
Regenerate: `python scripts/export_frontend_data.py` (about 3 min; runs the full test suite too)

| Number | Value | Key in the file |
| --- | --- | --- |
| ULB holdout size | 56,962 rows, 75 fraud | `datasets.ulb.holdout` |
| ULB global ROC-AUC / PR-AUC | 0.990 / 0.793 | `datasets.ulb.global.metrics.auc`, `.pr_auc` |
| ULB global recall / precision at 0.5 | 0.907 / 0.041 | `datasets.ulb.global.metrics.recall_at_0_5`, `.precision_at_0_5` |
| ULB single-bank average ROC-AUC / PR-AUC | 0.969 / 0.704 | `datasets.ulb.mean_local.auc`, `.pr_auc` |
| ULB per bank (rows, fraud, AUC, PR-AUC) | e.g. BANK-A 45,569 / 142 / 0.972 / 0.708 | `datasets.ulb.local[i]` |
| ULB global AUC by round | 0.977 → 0.990 | `datasets.ulb.rounds` |
| ULB ROC curve points | — | `datasets.ulb.global.roc` |
| Synthetic global vs single-bank AUC | 0.655 vs 0.572 | `datasets.synthetic.global.metrics.auc`, `datasets.synthetic.mean_local.auc` |
| DP sweep (σ, ε, AUC) | e.g. σ 8 → ε 7.79 | `privacy.rows` |
| Secure aggregation max error | 5.96e-8 | `secure_aggregation.max_abs_error_vs_plain_fedavg` |
| Byzantine attack, one run | FedAvg 0.502, Krum 0.588 | `robustness.fedavg_auc`, `robustness.krum_auc` |
| Load test, one run | memory / Kafka throughput | `load.memory`, `load.kafka` |
| Test suite | 939 passed, 0 skipped | `tests.passed`, `tests.skipped` |
| Lint and type check | clean | `checks.ruff`, `checks.mypy_strict` |

Also written by every `python -m aris.fl.run --dataset <name>` run (not committed):
`data/processed/m1_metrics_<dataset>.json` (same metrics) and the model
checkpoint `data/processed/m1_global_<dataset>.npz`.

## 2. Five-seed averages and 95% intervals (slides)

File: `docs/results/ulb_seeds.json`
Regenerate: `python scripts/stats/ulb_seeds.py` (about 1 min)

| Number | Value | Key |
| --- | --- | --- |
| Global ROC-AUC, 5 seeds | 0.987 ± 0.004 | `five_seed_summary.global_auc` |
| Global PR-AUC, 5 seeds | 0.786 ± 0.013 | `five_seed_summary.global_pr_auc` |
| Single-bank ROC-AUC, 5 seeds | 0.961 ± 0.006 | `five_seed_summary.mean_local_auc` |
| Single-bank PR-AUC, 5 seeds | 0.734 ± 0.017 | `five_seed_summary.mean_local_pr_auc` |
| Each seed's values | — | `per_seed` |
| Seed 42, 95% interval, global PR-AUC | [0.700, 0.874] | `seed42_bootstrap_95ci.global_pr_auc` |
| Seed 42, 95% interval, PR-AUC gain | [+0.041, +0.130] | `seed42_bootstrap_95ci.pr_auc_gain` |
| Seed 42, 95% interval, AUC gain | [+0.008, +0.041] | `seed42_bootstrap_95ci.auc_gain` |

## 3. Byzantine attack by attacker share

File: `docs/results/byzantine_sweep.json`
Regenerate: `python scripts/stats/byzantine_sweep.py` (under 1 min)

| Number | Value | Key |
| --- | --- | --- |
| FedAvg / median / Krum, 0–4 attackers | e.g. 1 attacker: 0.498 / 0.609 / 0.585 | `rows[i].fedavg.mean`, `.coordinate_median.mean`, `.krum.mean` |
| Per-seed values | — | `rows[i].<strategy>.per_seed` |
| Krum not run at 40% | null | `rows[4].krum` |

## 4. End-to-end replay (Table X)

File: `docs/results/replay.json`
Regenerate: `python scripts/stats/replay.py` (under 1 min)

| Number | Value | Key |
| --- | --- | --- |
| Fraud blocked, no network / ARIS | 270.4 / 285.8 of 300 | `mean.local.fraud_block`, `mean.aris.fraud_block` |
| Fraud let through, no network / ARIS | 29.6 / 14.2 | `mean.local.fraud_allow`, `mean.aris.fraud_allow` |
| Legit blocked / stepped up | 0 / 0.4 | `mean.*.legit_block`, `mean.*.legit_step_up` |
| Per seed | — | `per_seed` |

## 5. Load test, repeated at identical load

File: `docs/results/load_repeat.json`
Regenerate: `python scripts/stats/load_repeat.py` (needs Kafka; about 1 min)

| Number | Value | Key |
| --- | --- | --- |
| Memory, 10 × 50 and 20 × 200 | about 22,000–24,000 signals/s | `summary[*]` where `backend = memory` |
| Kafka, 10 × 50 | about 630 signals/s (510–700) | `summary[*]` where `backend = kafka`, `load = 10 x 50` |
| Kafka, 20 × 200 | about 2,000 signals/s (1,780–2,100) | `summary[*]` where `backend = kafka`, `load = 20 x 200` |
| Lost updates | 0 | `summary[*].lost_updates` |
| Every run with latencies | — | `runs` |

Each Kafka run uses a fresh topic. On a topic grown by earlier runs the same
test is much slower (28 MB of old runs: about 115 signals/s), because each new
reader catches up from the start of the topic.

## 6. DP sweep as a standalone run

Regenerate: `python -m aris.fl.privacy_sweep --dataset synthetic`
Writes: `data/processed/m2_privacy_utility_synthetic.json` (not committed).
DP noise is not seeded, so AUC at a given σ changes by up to about 0.03
between runs; ε does not change.

## 7. Live checks (run during the demo)

| What | Command | Output |
| --- | --- | --- |
| All policy cases | Live page → Policy cases → Run all cases | `10/10 match` |
| Three processes on real Kafka with OPRF | `python scripts/e2e_live_oprf.py` | `ALL CHECKS PASSED` |
| mTLS and broker ACLs | `python scripts/review_demo_mtls_acl.py` | BANK-EVIL publish refused |
| Key can't leave the HSM | `python scripts/review_demo_hsm.py` | `REFUSED by the token` |
| Model scores new inputs | `python scripts/review_demo_unseen_scoring.py` | f0 79, f3 93, f6 24 |
| Full test suite | `python -m pytest -q -p no:warnings` | `939 passed` |
