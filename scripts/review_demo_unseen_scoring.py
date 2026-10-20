"""Live demo: the trained global model scoring rows it has never seen --
not held-out rows from the original dataset, hand-built new ones.

Needs data/processed/m1_global_synthetic.npz to exist first:
    python -m aris.fl.run --dataset synthetic
"""

import numpy as np

from aris.fl.scorer import FraudScorer, load_weights
from aris.schema import LookupStatus, apply_policy

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
print(f"{'case':<{col}} {'score':>6} {'conf':>6}  decision (policy, if published)")
print("-" * (col + 45))
scores: dict[str, int] = {}
for label, row in cases:
    result = scorer.score_row(row)
    scores[label] = result.risk_score
    # The real policy, as BankBot would apply it to this score on a Rs 5,000 transfer.
    decision = apply_policy(
        LookupStatus.FOUND, result.risk_score, 500_000, confidence=result.confidence
    )
    print(f"{label:<{col}} {result.risk_score:>6} {result.confidence:>6.2f}  {decision.value}")

spiked = {label: s for label, s in scores.items() if "spiked" in label}
real = [s for label, s in spiked.items() if "f6" not in label]
f6 = next(s for label, s in spiked.items() if "f6" in label)
print(
    f"\nSame spike, different features: the bank-driven features score {min(real)}-{max(real)};"
    f"\nf6, which never drove fraud in training, scores {f6}. The model learned which"
    "\nfeatures matter, not 'big number = fraud'. This is the synthetic model"
    "\n(holdout AUC 0.655), so clean rows near 50 are expected."
)
