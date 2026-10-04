"""Additional synthetic deceased people for the multi-family demo.

Each person is derived from the Margaret Ellis template with their own name, city,
relatives and set of accounts, then written to data/estates/<slug>/. Everything is
synthetic and uses example addresses. Generation is deterministic for a given demo
date, so every laptop that shares the root inbox produces identical files.
"""
from __future__ import annotations

import argparse
import copy
import csv
import io
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from config import slugify
from generate_inbox import _BANK_DESCRIPTORS, ADDED_ACCOUNTS, BASELINE_ACCOUNTS, generate
from secure_storage import ensure_private_directory, read_json, write_json, write_text

PEOPLE: list[dict[str, Any]] = [
    {
        "name": "Harold Bennett", "age": 81, "city": "Grand Rapids, Michigan", "pronoun": "he",
        "bio": "Retired machinist and church choir bass. Father of Linda and Michael. Linda is his executor.",
        "relatives": [{"name": "Linda", "relationship": "daughter", "executor": True},
                      {"name": "Michael", "relationship": "son", "executor": False}],
        "drop": {"Spotify", "Peacock", "Hulu", "Coinbase", "Ypsi Yoga Collective", "Planet Fitness"},
    },
    {
        "name": "Rosa Martinez", "age": 68, "city": "Lansing, Michigan", "pronoun": "she",
        "bio": "Retired school nurse and community garden organizer. Wife of Carlos and mother of Elena. Elena is her executor.",
        "relatives": [{"name": "Elena", "relationship": "daughter", "executor": True},
                      {"name": "Carlos", "relationship": "husband", "executor": False}],
        "drop": {"Audible", "Dropbox", "Vanguard", "Delta SkyMiles", "The New York Times"},
    },
    {
        "name": "James Okafor", "age": 59, "city": "Kalamazoo, Michigan", "pronoun": "he",
        "bio": "High school chemistry teacher and weekend cyclist. Husband of Grace and father of David. Grace is his executor.",
        "relatives": [{"name": "Grace", "relationship": "wife", "executor": True},
                      {"name": "David", "relationship": "son", "executor": False}],
        "drop": {"MPSERS", "Social Security", "Hulu", "iCloud+", "Calm", "Paramount+"},
    },
    {
        "name": "Eleanor Whitfield", "age": 88, "city": "Traverse City, Michigan", "pronoun": "she",
        "bio": "Retired librarian and lifelong birdwatcher. Mother of Thomas and grandmother of Anne. Thomas is her executor.",
        "relatives": [{"name": "Thomas", "relationship": "son", "executor": True},
                      {"name": "Anne", "relationship": "granddaughter", "executor": False}],
        "drop": {"Spotify", "Planet Fitness", "Coinbase", "PayPal", "Peacock", "Xfinity"},
    },
]

_ACCOUNTS = BASELINE_ACCOUNTS + ADDED_ACCOUNTS
_INCOME_ROWS = {"MetLife": "METLIFE PREM", "Social Security": "SSA TREAS 310", "MPSERS": "MPSERS PENSION"}


def _senders_to_institutions() -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for account in _ACCOUNTS:
        if account["sender"]:
            mapping.setdefault(account["sender"], set()).add(account["institution"])
    return mapping


def build(person: dict[str, Any], today: date | str | None = None) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Return (inbox, truth, bank rows) for one person, without writing files."""
    inbox, truth, rows = generate(today)
    inbox, truth, rows = copy.deepcopy(inbox), copy.deepcopy(truth), copy.deepcopy(rows)
    drop = set(person["drop"])
    first, surname = person["name"].split()[0], person["name"].split()[-1]
    executor = next(relative["name"] for relative in person["relatives"] if relative["executor"])
    other = next(relative["name"] for relative in person["relatives"] if not relative["executor"])
    town = person["city"].split(",")[0]

    # Remove every email from a dropped account. A shared sender (Apple's bundled receipt,
    # Chase's two accounts) is removed only when all of its accounts are dropped.
    senders = _senders_to_institutions()
    emails = [email for email in inbox["emails"] if not (email["from"] in senders and senders[email["from"]] <= drop)]

    def personalize(text: str) -> str:
        return (text.replace("Daniel Ellis is the named beneficiary", f"{executor} {surname} is the named beneficiary")
                .replace("A note from Sarah", f"A note from {other}")
                .replace("Ann Arbor", town)
                .replace("Margaret", first))

    for email in emails:
        email.pop("id", None)
        email["subject"], email["body"] = personalize(email["subject"]), personalize(email["body"])
    # Keep the same inbox size with ordinary community newsletters.
    start = date.fromisoformat(inbox["today"])
    index = 0
    while len(emails) < len(inbox["emails"]):
        emails.append({"from": f"newsletter{index % 35}@community.example", "subject": f"{town} community news",
                       "date": (start - timedelta(days=30 + index * 7)).isoformat(),
                       "body": f"Hello {first}, here is this week's community news. There are no purchases or financial services in this message."})
        index += 1
    emails.sort(key=lambda item: (item["date"], item["from"], item["subject"], item["body"]))
    for position, email in enumerate(emails):
        email["id"] = f"msg_{position:04d}"

    # Remove the dropped accounts' bank rows and renumber the statement.
    dropped_descriptors = {_BANK_DESCRIPTORS[name] for name in drop if name in _BANK_DESCRIPTORS}
    dropped_prefixes = tuple(prefix for name, prefix in _INCOME_ROWS.items() if name in drop)
    kept = []
    for row in rows:
        if row["description"] in dropped_descriptors or (dropped_prefixes and row["description"].startswith(dropped_prefixes)):
            continue
        row = dict(row, description=row["description"].replace("ANN ARBOR", town.upper()))
        row.pop("id", None)
        kept.append(row)
    for position, row in enumerate(kept):
        row["id"] = f"bank_{position:03d}"

    truth["accounts"] = [account for account in truth["accounts"] if account["institution"] not in drop]
    truth["expected_accounts"] = len(truth["accounts"])
    truth["baseline_accounts"] = sum(1 for account in truth["accounts"] if account["baseline"])
    inbox["emails"] = emails
    inbox["persona"] = {
        "name": person["name"], "email": f"{first.lower()}.{surname.lower()}@example.com", "age": person["age"],
        "city": person["city"], "date_of_death": inbox["persona"]["date_of_death"], "bio": person["bio"],
        "pronoun": person["pronoun"], "executor": executor,
        "relatives": copy.deepcopy(person["relatives"]),
    }
    return inbox, truth, kept


def write(directory: Path, inbox: dict[str, Any], truth: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    ensure_private_directory(directory)
    write_json(directory / "inbox.json", inbox)
    write_json(directory / "truth.json", truth)
    with io.StringIO(newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "date", "description", "amount"])
        writer.writeheader()
        writer.writerows(rows)
        write_text(directory / "bank.csv", handle.getvalue())


def ensure(root: Path, today: str | None) -> list[str]:
    """Create or refresh each person's estate for the root's demo date; returns the slugs written."""
    written = []
    for person in PEOPLE:
        directory = Path(root) / "estates" / slugify(person["name"])
        try:
            current = read_json(directory / "inbox.json")
            if current.get("today") == today and current.get("persona", {}).get("name") == person["name"]:
                continue
        except (OSError, ValueError):
            pass
        write(directory, *build(person, today))
        written.append(directory.name)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the additional synthetic deceased people.")
    parser.add_argument("--root", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--today", help="Demo date YYYY-MM-DD; defaults to the root inbox's date.")
    args = parser.parse_args()
    today = args.today or read_json(args.root / "inbox.json").get("today")
    for person in PEOPLE:
        write(args.root / "estates" / slugify(person["name"]), *build(person, today))
        print(f"Created {person['name']}: relatives " + ", ".join(f"{r['name']} ({r['relationship']})" for r in person["relatives"]))


if __name__ == "__main__":
    main()
