"""M1 federated learning settings. Bank IDs match the project report."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BANK_IDS = ("BANK-A", "BANK-B", "BANK-C", "BANK-D", "BANK-E")

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_RAW = REPO_ROOT / "data" / "raw"
DATA_PROCESSED = REPO_ROOT / "data" / "processed"

ULB_FILENAME = "creditcard.csv"
ULB_URL = "https://storage.googleapis.com/download.tensorflow.org/data/creditcard.csv"

PAYSIM_FILENAMES = (
    "paysim.csv",
    "PS_20174392719_1491204439457_log.csv",
)
IEEE_CIS_TRANSACTION = "train_transaction.csv"


@dataclass(frozen=True)
class TrainConfig:
    num_banks: int = 5
    rounds: int = 8
    local_epochs: int = 4
    batch_size: int = 256
    learning_rate: float = 0.05
    holdout_frac: float = 0.2
    dirichlet_alpha: float = 0.5
    seed: int = 42
    hidden: int = 16
    max_train_rows: int | None = None  # cap ULB for a faster laptop run

    # M2 -- differential privacy (DP-SGD, see aris.fl.privacy). Off by default so
    # M1 behavior is unchanged unless a caller opts in.
    dp_enabled: bool = False
    max_grad_norm: float = 1.0
    noise_multiplier: float = 1.0
    dp_delta: float = 1e-5

    # M2 -- optional secure aggregation (see aris.fl.secure_agg). Independent of
    # dp_enabled: hides individual updates from the aggregator, but says nothing
    # about what the aggregate model itself can leak -- that's DP's job.
    secure_agg: bool = False

    # M6+ -- Byzantine-robust aggregation (see aris.fl.robust_agg), an
    # alternative to plain FedAvg's weighted mean. "fedavg" (default) is not
    # robust to any malicious client; "krum" and "coordinate_median" are, up to
    # a bound each documents. Mutually exclusive with secure_agg: masking
    # (secure_agg) and robust selection/reduction (this) both need to see or
    # act on the *set* of updates in ways the other's transform breaks --
    # combining them is a real, unsolved research question, not a supported
    # configuration here.
    aggregation_strategy: str = "fedavg"
    # Assumed number of Byzantine clients "krum" tolerates. Only meaningful
    # for that strategy; validated against num_banks below.
    num_byzantine: int = 1

    def __post_init__(self) -> None:
        # Validated here, at construction time, rather than left to fail deep
        # inside training or the epsilon accountant: a bad value below doesn't
        # just crash eventually, it can run a full (expensive) training loop
        # first and even persist a checkpoint before anything complains -- or,
        # worse, never raise at all and silently produce a misleadingly clean
        # report (see rounds/local_epochs below).
        if self.rounds < 1:
            raise ValueError(f"rounds must be >= 1, got {self.rounds}")
        if self.local_epochs < 1:
            raise ValueError(f"local_epochs must be >= 1, got {self.local_epochs}")
        if self.dp_enabled:
            if self.noise_multiplier <= 0:
                raise ValueError(
                    "noise_multiplier must be positive when dp_enabled=True, got "
                    f"{self.noise_multiplier}. A zero or negative value adds no (or "
                    "negative) Gaussian noise -- clip_and_noise_gradients would run "
                    "with unprotected clipped gradients while the report still claims "
                    "dp_enabled=True, and the accountant only notices at report time, "
                    "after training already happened."
                )
            if self.max_grad_norm <= 0:
                raise ValueError(f"max_grad_norm must be positive, got {self.max_grad_norm}")
            if not (0.0 < self.dp_delta < 1.0):
                raise ValueError(f"dp_delta must be in (0, 1), got {self.dp_delta}")
        valid_strategies = ("fedavg", "krum", "coordinate_median")
        if self.aggregation_strategy not in valid_strategies:
            raise ValueError(
                f"aggregation_strategy must be one of {valid_strategies}, "
                f"got {self.aggregation_strategy!r}"
            )
        if self.aggregation_strategy == "krum" and self.num_banks <= 2 * self.num_byzantine + 2:
            raise ValueError(
                f"krum needs num_banks > 2*num_byzantine + 2 to guarantee robustness "
                f"(num_banks={self.num_banks}, num_byzantine={self.num_byzantine})"
            )
        if self.secure_agg and self.aggregation_strategy != "fedavg":
            raise ValueError(
                "secure_agg and aggregation_strategy are mutually exclusive: masking "
                "(secure_agg) and robust selection (krum/coordinate_median) both need "
                "to act on the set of updates in ways the other's transform breaks."
            )
