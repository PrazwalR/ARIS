# Phase M5: Explainability (SHAP) & MLOps Governance Report

**Status:** Completed & Evaluated
**Tooling:** Permutation SHAP (`FraudExplainer`), Evidently K-S Drift (`check_drift`), Versioned Model Registry.

## 1. Model Explainability & Attribution
- **Target Fraud Sample:** Driven by synthetic spikes on `f3` and `f0`.
- **SHAP Dominant Feature Attributed:** `f3` (Mapped to reason codes: `high_velocity`, `new_beneficiary`).
- **Analyst Defensibility:** Every blocked transaction produces a deterministic attribution trace to satisfy regulatory inquiries.

## 2. Production Drift Monitoring & Rollback
- **Data Drift Detection:** Kolmogorov-Smirnov test correctly isolated drifted feature (`['f1']`) while clearing untouched features.
- **Automated Rollback Safeguard:** `should_rollback = True` when an incoming candidate model regressed AUC from 0.98 to 0.85.
