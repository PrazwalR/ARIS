from __future__ import annotations

import numpy as np
from flwr.client import NumPyClient

from aris.fl.model import get_weights, new_model, set_weights, train_local


class BankFlowerClient(NumPyClient):
    """Flower client: local rows never leave this object. Only weight arrays go out."""

    def __init__(
        self,
        bank_id: str,
        x: np.ndarray,
        y: np.ndarray,
        hidden: int,
        seed: int,
    ) -> None:
        self.bank_id = bank_id
        self.x = x
        self.y = y
        self.hidden = hidden
        self.seed = seed
        self.model = new_model(x.shape[1], hidden=hidden, seed=seed)

    def get_parameters(self, _config: dict) -> list[np.ndarray]:
        return get_weights(self.model)

    def fit(self, parameters: list[np.ndarray], config: dict) -> tuple[list[np.ndarray], int, dict]:
        set_weights(self.model, parameters)
        metrics = train_local(
            self.model,
            self.x,
            self.y,
            epochs=int(config.get("local_epochs", 1)),
            batch_size=int(config.get("batch_size", 256)),
            lr=float(config.get("learning_rate", 0.05)),
            seed=self.seed + int(config.get("server_round", 0)),
        )
        metrics["bank_id"] = self.bank_id
        return get_weights(self.model), len(self.y), metrics

    def evaluate(self, parameters: list[np.ndarray], _config: dict) -> tuple[float, int, dict]:
        set_weights(self.model, parameters)
        return 0.0, len(self.y), {"bank_id": self.bank_id}
