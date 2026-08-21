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
