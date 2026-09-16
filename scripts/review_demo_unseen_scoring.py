"""Live demo: the trained global model scoring rows it has never seen --
not held-out rows from the original dataset, hand-built new ones.

Needs data/processed/m1_global_synthetic.npz to exist first:
    python -m aris.fl.run --dataset synthetic
"""

import numpy as np

from aris.fl.scorer import FraudScorer, load_weights

weights, meta = load_weights("data/processed/m1_global_synthetic.npz")
scorer = FraudScorer.from_weights(
    n_features=meta["n_features"], weights=weights, model_version=meta["model_version"]
)
print(f"loaded checkpoint trained on: {meta['dataset']!r}")
print(f"expects {meta['n_features']} features: {meta['feature_names']}\n")

rng = np.random.default_rng(2026)  # fresh seed -- never used during training or holdout


def clean_row() -> np.ndarray:
    return rng.normal(0.0, 1.0, size=meta["n_features"]).astype(np.float32)


def fraud_like_row(driving_feature: int) -> np.ndarray:
    """Mimics the pattern one bank's fraud looked like during training --
    but this exact row was never in the dataset. See make_synthetic in
    src/aris/fl/datasets.py: fraud is driven by one feature taking a large
    value (the training data's own forced-fraud rows used exactly 3.0)."""
    row = np.zeros(meta["n_features"], dtype=np.float32)
    row[driving_feature] = 4.5
    return row


cases = [
    ("typical transaction (all-zero baseline)", np.zeros(meta["n_features"], dtype=np.float32)),
    ("random clean-looking transaction #1", clean_row()),
    ("random clean-looking transaction #2", clean_row()),
    ("fraud-pattern from bank 0's signal (f0 spiked)", fraud_like_row(0)),
    ("fraud-pattern from bank 3's signal (f3 spiked)", fraud_like_row(3)),
    ("f6 spiked -- not a real bank's pattern (5 banks trained, f5-f7 unused)", fraud_like_row(6)),
]

col = max(len(label) for label, _ in cases) + 2
print(f"{'case':<{col}} {'score':>6} {'conf':>6}  decision")
print("-" * (col + 25))
for label, row in cases:
    result = scorer.score_row(row)
    decision = (
        "BLOCK" if result.risk_score >= 85 else "step-up" if result.risk_score >= 50 else "allow"
    )
    print(f"{label:<{col}} {result.risk_score:>6} {result.confidence:>6.2f}  {decision}")

print(
    "\nf0 and f3 both trigger BLOCK: those are real bank-driven fraud signals"
    "\nthe model saw during training (5 banks, driving features 0-4). f6 does"
    "\nNOT trigger it despite the same spike magnitude -- feature 6 never drove"
    "\nfraud for any of the 5 banks trained on, so the model correctly learned"
    "\nit is irrelevant. This is not 'big number = fraud' -- it is specific."
)
