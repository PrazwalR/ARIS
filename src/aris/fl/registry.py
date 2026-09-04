"""M5: model registry -- track trained checkpoints across versions with an
explicit "active" pointer and rollback criteria, instead of each training run
silently overwriting the single checkpoint file `aris.fl.run` writes.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aris.fl.config import DATA_PROCESSED

MANIFEST_FILENAME = "model_registry.json"
DEFAULT_ROLLBACK_METRIC = "auc"
DEFAULT_MAX_REGRESSION = 0.02


@dataclass(frozen=True)
class ModelRecord:
    model_version: str
    dataset: str
    checkpoint_path: str
    metrics: dict[str, float]
    registered_at: str  # ISO 8601 UTC
    notes: str = ""


class ModelRegistry:
    """A JSON manifest under `registry_dir` (default `data/processed/`), written
    atomically the same way `aris.fl.run.write_report` is -- a crash mid-write
    must never corrupt or silently roll back which version is "active".
    """

    def __init__(self, registry_dir: Path | str | None = None) -> None:
        self.registry_dir = Path(registry_dir) if registry_dir else DATA_PROCESSED
        self.manifest_path = self.registry_dir / MANIFEST_FILENAME
        self._records: dict[str, ModelRecord] = {}
        self._active_version: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.manifest_path.exists():
            return
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self._records = {v: ModelRecord(**r) for v, r in data.get("records", {}).items()}
        self._active_version = data.get("active_version")

    def _save(self) -> None:
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": {v: asdict(r) for v, r in self._records.items()},
            "active_version": self._active_version,
        }
        text = json.dumps(payload, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.registry_dir, prefix=f".{MANIFEST_FILENAME}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_name, self.manifest_path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def register(
        self,
        model_version: str,
        dataset: str,
        checkpoint_path: Path | str,
        metrics: dict[str, float],
        notes: str = "",
        activate: bool = True,
    ) -> ModelRecord:
        if model_version in self._records:
            raise ValueError(f"model_version {model_version!r} is already registered")
        record = ModelRecord(
            model_version=model_version,
            dataset=dataset,
            checkpoint_path=str(checkpoint_path),
            metrics=dict(metrics),
            registered_at=datetime.now(timezone.utc).isoformat(),
            notes=notes,
        )
        self._records[model_version] = record
        if activate or self._active_version is None:
            self._active_version = model_version
        self._save()
        return record

    @property
    def active_version(self) -> str | None:
        return self._active_version

    def active_record(self) -> ModelRecord | None:
        if self._active_version is None:
            return None
        return self._records[self._active_version]

    def get(self, model_version: str) -> ModelRecord | None:
        return self._records.get(model_version)

    def history(self) -> tuple[ModelRecord, ...]:
        return tuple(sorted(self._records.values(), key=lambda r: r.registered_at))

    def rollback_to(self, model_version: str) -> ModelRecord:
        """Point `active_version` at an already-registered, presumably older,
        version. Does not delete or unregister the version being rolled back
        from -- it stays in history, just no longer active.
        """
        if model_version not in self._records:
            raise ValueError(f"no registered model_version {model_version!r} to roll back to")
        self._active_version = model_version
        self._save()
        return self._records[model_version]


def should_rollback(
    candidate_metrics: dict[str, float],
    active_metrics: dict[str, float],
    metric: str = DEFAULT_ROLLBACK_METRIC,
    max_regression: float = DEFAULT_MAX_REGRESSION,
) -> bool:
    """Whether `candidate` regresses on `metric` enough, relative to the
    currently active model, to justify staying on (or reverting to) the active
    one instead of promoting the candidate.

    A missing metric on either side means "cannot compare" and returns False
    (not a rollback) -- treating absent data as a regression would trigger
    rollbacks for the wrong reason (a metric that was never computed, not one
    that got worse).
    """
    if metric not in candidate_metrics or metric not in active_metrics:
        return False
    return candidate_metrics[metric] < active_metrics[metric] - max_regression
