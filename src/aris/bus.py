"""In-memory Shared Risk-Signal Bus (M0). M3 replaces this with Kafka."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aris.schema import RiskSignal


class InMemoryRiskBus:
    def __init__(self) -> None:
        self._by_risk_id: dict[str, RiskSignal] = {}

    def publish(self, signal: RiskSignal) -> None:
        self._by_risk_id[signal.risk_id] = signal

    def lookup(self, risk_id: str) -> RiskSignal | None:
        signal = self._by_risk_id.get(risk_id)
        if signal is None:
            return None
        expiry = signal.timestamp + timedelta(hours=signal.ttl_hours)
        if datetime.now(timezone.utc) > expiry:
            del self._by_risk_id[risk_id]
            return None
        return signal
