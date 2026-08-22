from __future__ import annotations

import runpy

import pytest


def test_demo_runs_and_blocks_anus_transfer(capsys: pytest.CaptureFixture[str]):
    """The README points a reader at this demo, so it has to keep working."""
    runpy.run_module("aris.demo.anu_transfer", run_name="__main__")
    out = capsys.readouterr().out

    assert "decision        : block" in out
    assert "decision remains : block" in out
    assert "rejected" in out, "the forged retraction should be refused"
    assert "idempotent" in out

    # The bus payload is printed first; the account number must not appear in it,
    # even though the customer-facing section below legitimately shows it.
    bus_section = out.split("=== Bank A BankBot", 1)[0]
    assert "risk_id" in bus_section, "guard is anchored to the bus payload section"
    assert "ACC-999" not in bus_section
