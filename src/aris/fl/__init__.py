"""Federated fraud training (M1): Flower clients, FedAvg, local vs global metrics."""

__all__ = ["run_experiment"]


def __getattr__(name: str):
    if name == "run_experiment":
        from aris.fl.run import run_experiment

        return run_experiment
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
