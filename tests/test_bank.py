from __future__ import annotations

import csv

import pytest

import bank


def write_csv(path, dates, amounts, description="SQ *EXAMPLE CLUB ANN ARBOR MI"):
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["id", "date", "description", "amount"])
        writer.writeheader()
        for index, (day, amount) in enumerate(zip(dates, amounts, strict=True)):
            writer.writerow({"id": f"bank_{index:03d}", "date": day, "description": description, "amount": amount})


def test_monthly_detection_and_normalization(tmp_path):
    path = tmp_path / "bank.csv"
    write_csv(path, ["2026-07-01", "2026-08-01", "2026-09-01"], [-45, -45, -45])
    found = bank.detect(path)
    assert len(found) == 1 and found[0]["frequency"] == "monthly"
    assert found[0]["amount"] == 45 and found[0]["sources"] == ["bank"]
    assert bank.normalize_description("POS SQ *EXAMPLE CLUB 1234 ANN ARBOR MI") == "EXAMPLE CLUB"


@pytest.mark.parametrize("amounts", [[-20, -20, -21.01], [-10, -100, -1000]])
def test_amount_variation_rejected(tmp_path, amounts):
    path = tmp_path / "bank.csv"
    write_csv(path, ["2026-07-01", "2026-08-01", "2026-09-01"], amounts)
    assert bank.detect(path) == []


def test_annual_regular_credits_and_oneoffs(tmp_path):
    path = tmp_path / "bank.csv"
    write_csv(path, ["2023-09-01", "2024-09-01", "2025-09-01"], [-100, -100, -100])
    assert bank.detect(path)[0]["frequency"] == "annual"
    write_csv(path, ["2026-07-01", "2026-08-01", "2026-09-01"], [1500, 1500, 1500], "SSA TREAS 310")
    assert bank.detect(path)[0]["category"] == "government"
    write_csv(path, ["2026-07-01", "2026-07-02", "2026-07-03"], [-100, -100, -100])
    assert bank.detect(path) == []
