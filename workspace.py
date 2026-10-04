"""The family's workspace on top of a discovered estate: corrections, guides, next steps, digests and reports.

Everything here is a pure function of the saved estate (plus the inbox and activity it is given),
so every relative's screen and every export agree. Nothing here contacts a provider.
"""
from __future__ import annotations

import copy
import html
import re
import threading
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from email.utils import parseaddr
from typing import Any

import config
from models import ACTIONS, BUCKETS
from secure_storage import private_file_lock, read_json, write_json

_LOCK = threading.RLock()
CORRECTABLE = ("institution", "amount", "category", "frequency")

# ---- The family view -----------------------------------------------------------------------


def is_dismissed(account: dict[str, Any]) -> bool:
    return (account.get("review") or {}).get("state") == "dismissed"


def monthly_cost(account: dict[str, Any]) -> float:
    amount = float(account.get("amount") or 0)
    return amount / 12 if account.get("frequency") == "annual" else amount if account.get("frequency") == "monthly" else 0.0


def needs_review(account: dict[str, Any]) -> bool:
    """Thin evidence the family should glance at before acting on it."""
    if (account.get("review") or {}).get("state") in {"confirmed", "dismissed"} or "family" in account.get("sources", []):
        return False
    single_source = len(account.get("evidence_ids") or []) <= 1 and "bank" not in account.get("sources", [])
    unpriced_charge = account.get("bucket") == "leaving" and account.get("amount") is None
    return bool(single_source or unpriced_charge or not account.get("active", True))


def _company(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def family_view(estate: dict[str, Any]) -> dict[str, Any]:
    """Apply the family's reviewed corrections and dismissals, and recompute the totals from them."""
    result = copy.deepcopy(estate)
    for account in result.get("accounts", []):
        corrections = account.get("corrections") or {}
        original = {}
        for field in CORRECTABLE:
            if field in corrections and corrections[field] is not None and corrections[field] != account.get(field):
                original[field] = account.get(field)
                account[field] = corrections[field]
        if original:
            account["original"] = original
        if "category" in original:
            account["bucket"] = BUCKETS[account["category"]]
            account["action"] = ACTIONS[account["category"]]
        account["needs_review"] = needs_review(account)
    visible = [account for account in result.get("accounts", []) if not is_dismissed(account)]
    totals = dict(result.get("totals") or {})
    leaving = [account for account in visible if account["bucket"] == "leaving" and account.get("active", True)]
    totals["monthly_drain"] = round(sum(monthly_cost(account) for account in leaving), 2)
    # Older saved analyses lack per-account charges; their stored total stays authoritative.
    if all("charged_since_death" in account for account in result.get("accounts", [])):
        totals["charged_since_death"] = round(sum(float(account.get("charged_since_death") or 0) for account in leaving), 2)
    totals["assets_found"] = round(sum(float(account.get("amount") or 0) for account in visible
                                       if account["bucket"] == "waiting" and account.get("frequency") in {"balance", "one_time"}), 2)
    totals["debts_found"] = round(sum(float(account.get("amount") or 0) for account in visible if account["bucket"] == "owed"), 2)
    totals["death_certificates"] = len({_company(account["institution"]) for account in visible if account["category"] not in {"subscription", "utility"}})
    totals["accounts"] = len(visible)
    result["totals"] = totals
    return result


# ---- Action guides, documents and contact routes ----------------------------------------------

DOCUMENTS = {
    "death_certificate": "Certified death certificate",
    "death_certificate_copy": "Copy of the death certificate",
    "letters": "Letters testamentary or of administration (or a small estate affidavit)",
    "requester_id": "Your photo ID",
    "statement": "A recent statement, bill or account number",
    "claim_form": "The company's claim form",
    "beneficiary_tax": "Beneficiary's ID and taxpayer form (W-9)",
    "obituary": "Obituary or funeral notice",
    "relationship_proof": "Proof you are the executor or next of kin",
}
ACTION_GUIDES = {
    "cancel": {
        "goal": "Stop the charge and ask for a refund of anything billed after the date of death.",
        "steps": [
            "Find the account or member number on a receipt in the evidence below.",
            "Contact the company's support or bereavement team through its official website.",
            "Ask them to cancel the account and refund charges made after the date of death.",
            "Ask for written confirmation and a reference number, then record it here.",
            "Check the card or bank statement next month to make sure the charge stopped.",
        ],
        "documents": ["death_certificate_copy", "statement"],
    },
    "transfer": {
        "goal": "Close the service or move it to whoever now lives at the address, and settle the final bill.",
        "steps": [
            "Decide whether the service should be transferred to a new occupant or closed.",
            "Contact the provider and give a move-out or final meter reading date.",
            "Ask for the final bill to be addressed to the estate, not paid from a personal account.",
            "Record the confirmation or reference number here.",
        ],
        "documents": ["death_certificate_copy", "statement", "letters"],
    },
    "transfer_financial": {
        "goal": "Move the money to the named beneficiary or into the estate's account.",
        "steps": [
            "Tell the institution about the death and ask whether a beneficiary or transfer-on-death designation exists.",
            "Ask for their transfer or transmission forms and whether a medallion signature guarantee is needed.",
            "Send the forms with a certified death certificate and proof of your authority as executor.",
            "Record the confirmation here, then check that the funds arrived in the beneficiary's or estate's account.",
        ],
        "documents": ["death_certificate", "letters", "requester_id", "statement"],
    },
    "claim": {
        "goal": "Find out who inherits this money and start the claim.",
        "steps": [
            "Notify the institution of the death and ask whether a beneficiary, payable-on-death or transfer-on-death designation exists.",
            "Ask them to freeze the account against fraud and send their claim or transmission forms.",
            "Send the forms with a certified death certificate and proof of your authority.",
            "Track the claim number here; funds go to the named beneficiary or to the estate account.",
        ],
        "documents": ["death_certificate", "letters", "requester_id", "statement"],
    },
    "notify": {
        "goal": "Report the death so payments, benefits and records are updated.",
        "steps": [
            "Report the death and ask whether any payments received after the date of death must be returned.",
            "Ask about survivor or death benefits the family may be eligible for.",
            "For debts, ask for an estate balance statement. Family members are generally not personally liable; pay only from the estate after review.",
            "Record the reference number and any promised follow-up here.",
        ],
        "documents": ["death_certificate", "requester_id", "letters"],
    },
    "memorialize": {
        "goal": "Preserve or close the account according to the family's wishes.",
        "steps": [
            "Decide whether to memorialize, download memories from, or delete the account.",
            "Download photos and messages first if the family wants to keep them.",
            "Submit the company's deceased-user request through its official help pages.",
            "Record the request's confirmation here.",
        ],
        "documents": ["death_certificate_copy", "obituary", "relationship_proof"],
    },
}
# Official routes the team has verified. Everything else points to the company's own domain,
# proven by the evidence emails, never to a number found in an unsolicited message.
KNOWN_ROUTES = {
    "socialsecurity": {"label": "Call Social Security at 1-800-772-1213 (TTY 1-800-325-0778), or visit a local office. Funeral homes often report the death for you.", "url": "https://www.ssa.gov/"},
    "socialsecurityadministration": {"label": "Call Social Security at 1-800-772-1213 (TTY 1-800-325-0778), or visit a local office. Funeral homes often report the death for you.", "url": "https://www.ssa.gov/"},
    "facebook": {"label": "Use Facebook's memorialization request form.", "url": "https://www.facebook.com/help/contact/234739086860192"},
    "google": {"label": "Submit Google's request about a deceased user's account.", "url": "https://support.google.com/accounts/troubleshooter/6357590"},
    "googleaccount": {"label": "Submit Google's request about a deceased user's account.", "url": "https://support.google.com/accounts/troubleshooter/6357590"},
    "gmail": {"label": "Submit Google's request about a deceased user's account.", "url": "https://support.google.com/accounts/troubleshooter/6357590"},
    "appleaccount": {"label": "Use Apple's Digital Legacy pages to request access or account closure.", "url": "https://digital-legacy.apple.com/"},
    "apple": {"label": "Use Apple's Digital Legacy pages to request access or account closure.", "url": "https://digital-legacy.apple.com/"},
}


def sender_domain(account: dict[str, Any], emails: dict[str, dict]) -> str:
    """The company's real domain, taken from the evidence emails it sent."""
    domains = []
    for eid in account.get("evidence_ids") or []:
        address = parseaddr(str((emails.get(eid) or {}).get("from", "")))[1].lower()
        if "@" in address:
            parts = address.split("@", 1)[1].split(".")
            domains.append(".".join(parts[-2:]) if len(parts) >= 2 else address.split("@", 1)[1])
    return max(set(domains), key=domains.count) if domains else ""


def guide(account: dict[str, Any], emails: dict[str, dict] | None = None) -> dict[str, Any]:
    action = account["action"] if account["action"] in ACTION_GUIDES else "notify"
    # "Transfer" means a final bill for a utility, but moving money for a financial account.
    if action == "transfer" and account["category"] in {"bank", "investment", "crypto", "payment_app", "insurance", "pension"}:
        action = "transfer_financial"
    template = ACTION_GUIDES[action]
    documents = []
    for key in template["documents"]:
        name = DOCUMENTS[key]
        if key == "claim_form" and account["category"] != "insurance":
            continue
        documents.append({"name": name, "status": (account.get("documents") or {}).get(name, "needed")})
    if account["category"] == "insurance":
        documents.append({"name": DOCUMENTS["claim_form"], "status": (account.get("documents") or {}).get(DOCUMENTS["claim_form"], "needed")})
        documents.append({"name": DOCUMENTS["beneficiary_tax"], "status": (account.get("documents") or {}).get(DOCUMENTS["beneficiary_tax"], "needed")})
    # Documents the company itself asked for are added to the packet.
    for name, status in (account.get("documents") or {}).items():
        if not any(item["name"] == name for item in documents):
            documents.append({"name": name, "status": status})
    route = KNOWN_ROUTES.get(_company(account["institution"]))
    domain = sender_domain(account, emails or {})
    if route:
        contact = {**route, "verified": True}
    elif domain:
        contact = {"label": f"Use the help or bereavement pages on {domain}, the domain this company's emails came from. Do not use phone numbers from unexpected emails or texts.",
                   "url": f"https://www.{domain}/", "verified": True}
    else:
        contact = {"label": f"Search {account['institution']}'s official website for its bereavement or deceased-customer page.", "url": None, "verified": False}
    return {"goal": template["goal"], "steps": template["steps"], "documents": documents, "contact": contact}


# ---- Estate-level workspace file (documents on hand, mailbox connection) --------------------------

def _workspace_path():
    return config.data_dir() / "runtime_workspace.json"


@contextmanager
def workspace_locked():
    path = _workspace_path()
    with _LOCK, private_file_lock(path.with_name(".runtime-workspace.lock")):
        records = read_json(path) if path.exists() else {}
        if not isinstance(records, dict):
            records = {}
        yield records
        write_json(path, records)


def read_workspace() -> dict[str, Any]:
    path = _workspace_path()
    if not path.exists():
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


# ---- What should I do next? ---------------------------------------------------------------------

def _due(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def next_steps(estate: dict[str, Any], today: date, *, limit: int = 8) -> list[dict[str, Any]]:
    """One ordered list across every account: overdue promises, renewals, documents, then money."""
    items: list[dict[str, Any]] = []

    def add(priority: float, kind: str, title: str, detail: str, account: dict | None = None, due: date | None = None):
        items.append({"priority": priority, "kind": kind, "title": title, "detail": detail,
                      "account_id": account["id"] if account else None, "due": due.isoformat() if due else None})

    accounts = [account for account in estate.get("accounts", []) if not is_dismissed(account)]
    for account in accounts:
        for item in account.get("followups") or []:
            if item.get("done"):
                continue
            due = _due(item.get("due"))
            if due and due < today:
                add(0 + (today - due).days * -0.01, "overdue", f"Overdue: {item['title']}", f"{account['institution']} · was due {due:%b %d}", account, due)
            elif due and (due - today).days <= 7:
                add(20 + (due - today).days, "followup", item["title"], f"{account['institution']} · due {due:%b %d}", account, due)
        if account["status"] == "done":
            continue
        if account.get("urgent"):
            add(10 + (account.get("days_until") or 0), "renewal", f"Stop {account['institution']} before it renews",
                f"{_money(account.get('amount'))} may be charged in {account.get('days_until')} days", account, _due(account.get("next_date")))
        outcome = account.get("outcome") or {}
        outstanding = [name for name, status in (account.get("documents") or {}).items() if status == "needed"]
        if outcome.get("result") == "documents_required" or (outstanding and account["status"] == "in_progress"):
            add(30, "documents", f"Send documents to {account['institution']}",
                ", ".join(outstanding[:3]) if outstanding else "The company asked for documents before it can finish.", account)
        for note in account.get("notes") or []:
            if note.get("kind") == "help" and not note.get("resolved"):
                add(35, "help", f"{note['author']} asked for help with {account['institution']}", note["text"][:140], account)
    review = [account for account in accounts if account.get("needs_review")]
    if review:
        add(40, "review", f"Review {len(review)} finding{'s' if len(review) != 1 else ''}",
            "Confirm or dismiss accounts with thin evidence before acting on them.")
    open_accounts = [account for account in accounts if account["status"] != "done"]
    for account in sorted((a for a in open_accounts if a["bucket"] == "leaving" and a.get("active", True) and not a.get("urgent")), key=monthly_cost, reverse=True)[:3]:
        if monthly_cost(account):
            add(50 - min(monthly_cost(account), 49) / 10, "charge", f"Stop {account['institution']}", f"{_money(monthly_cost(account))} a month is still being charged", account)
    for account in sorted((a for a in open_accounts if a["bucket"] == "waiting" and a.get("amount")), key=lambda a: a["amount"], reverse=True)[:3]:
        who = f"Assigned to {account['assigned_to']}" if account.get("assigned_to") else "No one is assigned yet"
        add(60, "claim", f"Start the claim with {account['institution']}", f"{_money(account['amount'])} waiting · {who}", account)
    for account in (a for a in open_accounts if a["bucket"] == "notify"):
        add(70, "notify", f"Notify {account['institution']}", "Report the death so payments and records are updated.", account)
    items.sort(key=lambda item: item["priority"])
    seen, ordered = set(), []
    for item in items:
        key = (item["kind"], item["account_id"], item["title"])
        if key not in seen:
            seen.add(key)
            ordered.append({k: v for k, v in item.items() if k != "priority"})
    return ordered[:limit]


def _money(value: Any) -> str:
    if value is None:
        return "An amount"
    amount = float(value)
    return f"${amount:,.2f}" if amount % 1 else f"${amount:,.0f}"


# ---- Weekly family digest -------------------------------------------------------------------------

def digest(estate: dict[str, Any], activity: list[dict[str, Any]], today: date, *, days: int = 7) -> dict[str, Any]:
    start = today - timedelta(days=days)
    names = {account["id"]: account["institution"] for account in estate.get("accounts", [])}
    recent = []
    for row in activity:
        try:
            when = datetime.fromisoformat(str(row.get("created_at"))).date()
        except ValueError:
            continue
        if when >= start:
            recent.append(row)
    completed = sorted({names.get(row.get("account_id"), "An account") for row in recent if row.get("action") == "status:done"})
    accounts = [account for account in estate.get("accounts", []) if not is_dismissed(account)]
    upcoming = []
    for account in accounts:
        if account["status"] != "done" and account.get("urgent") and account.get("next_date"):
            upcoming.append({"date": account["next_date"], "title": f"{account['institution']} renews ({_money(account.get('amount'))})", "account_id": account["id"]})
        for item in account.get("followups") or []:
            due = _due(item.get("due"))
            if not item.get("done") and due and due <= today + timedelta(days=days):
                upcoming.append({"date": due.isoformat(), "title": f"{item['title']} ({account['institution']})", "account_id": account["id"], "overdue": due < today})
    upcoming.sort(key=lambda item: item["date"])
    waiting = defaultdict(list)
    for account in accounts:
        if account["status"] != "done" and account.get("assigned_to"):
            waiting[account["assigned_to"]].append(account["institution"])
    help_requests = [{"author": note["author"], "institution": account["institution"], "text": note["text"]}
                     for account in accounts for note in account.get("notes") or [] if note.get("kind") == "help" and not note.get("resolved")]
    people = sorted({str(row.get("actor")) for row in recent if row.get("actor")})
    done = sum(1 for account in accounts if account["status"] == "done")
    lines = [f"Lastly weekly digest · {estate['persona'].get('name', 'The estate')} · week ending {today:%B %d, %Y}", ""]
    lines.append(f"Progress: {done} of {len(accounts)} accounts done.")
    lines.append(f"Completed this week: {', '.join(completed) if completed else 'nothing marked done yet'}.")
    if upcoming:
        lines.append("Coming up:")
        lines += [f"  • {item['date']}: {item['title']}{' (overdue)' if item.get('overdue') else ''}" for item in upcoming]
    if waiting:
        lines.append("Waiting on someone:")
        lines += [f"  • {person}: {', '.join(sorted(items))}" for person, items in sorted(waiting.items())]
    if help_requests:
        lines.append("Asked for help:")
        lines += [f"  • {item['author']} on {item['institution']}: {item['text']}" for item in help_requests]
    lines.append(f"{len(recent)} updates from {', '.join(people) if people else 'the family'} this week.")
    return {"start": start.isoformat(), "end": today.isoformat(), "completed": completed, "upcoming": upcoming,
            "waiting": [{"person": person, "accounts": sorted(items)} for person, items in sorted(waiting.items())],
            "help": help_requests, "updates": len(recent), "people": people, "done": done, "total": len(accounts),
            "text": "\n".join(lines)}


# ---- How each bill is paid -------------------------------------------------------------------------

_CARD = re.compile(r"\b(?:visa|mastercard|amex|american express|discover|debit card|credit card|card)\b[^.\n]{0,24}?(?:ending(?:\s+in)?|•+|\*+|x+)\s*(\d{4})\b", re.IGNORECASE)


def funding_map(estate: dict[str, Any], emails: dict[str, dict]) -> dict[str, Any]:
    """Which account or card pays each recurring charge, so the family can close things in order."""
    accounts = [account for account in estate.get("accounts", []) if not is_dismissed(account)]
    banks = [account for account in accounts if account["category"] == "bank"]
    # Card and account numbers mentioned in each financial account's own evidence.
    owners: dict[str, dict] = {}
    for account in accounts:
        if account["category"] in {"bank", "debt"}:
            for eid in account.get("evidence_ids") or []:
                for number in re.findall(r"\b(?:ending(?:\s+in)?|•+|\*+)\s*(\d{4})\b", str((emails.get(eid) or {}).get("body", "")), re.IGNORECASE):
                    owners.setdefault(number, account)
    groups: dict[str, dict] = {}

    def group(key: str, label: str, account: dict | None = None, note: str = "") -> dict:
        if key not in groups:
            groups[key] = {"funder": label, "account_id": account["id"] if account else None, "note": note, "monthly_total": 0.0, "charges": []}
        return groups[key]

    for account in accounts:
        if account["bucket"] != "leaving" or not account.get("active", True):
            continue
        bodies = " ".join(str((emails.get(eid) or {}).get("body", "")) for eid in account.get("evidence_ids") or [])
        senders = " ".join(str((emails.get(eid) or {}).get("from", "")) for eid in account.get("evidence_ids") or [])
        card = _CARD.search(bodies)
        if card and card.group(1) in owners:
            owner = owners[card.group(1)]
            target = group(owner["id"], f"{owner['institution']} (ending {card.group(1)})", owner)
        elif card:
            target = group(f"card-{card.group(1)}", f"Card ending {card.group(1)}", note="This card was not found among the discovered accounts. Check the wallet or credit report.")
        elif "apple.com" in senders.lower():
            apple = next((a for a in accounts if _company(a["institution"]) in {"apple", "appleaccount", "appleid"}), None)
            target = group("apple", "Billed through the Apple Account", apple, note="Cancel these in the Apple Account's subscriptions, not with each app.")
        elif "bank" in account.get("sources", []):
            # The statement belongs to a checking account; prefer one the family didn't add by hand.
            discovered = [item for item in banks if "family" not in item.get("sources", [])]
            checking = [item for item in discovered if "checking" in item["institution"].casefold()]
            bank = (checking or discovered or [None])[0] if len(checking) == 1 or len(discovered) == 1 else None
            target = group(bank["id"] if bank else "bank", f"{bank['institution']} (bank statement)" if bank else "The bank statement account", bank)
        elif re.search(r"\bautopay\b|\bdrafted\b|\bdirect debit\b", bodies, re.IGNORECASE):
            target = group("autopay", "AutoPay from an account not yet identified", note="The bills mention AutoPay. A bank statement would show which account pays them.")
        else:
            target = group("unknown", "Payment method not found in the evidence")
        target["charges"].append({"account_id": account["id"], "institution": account["institution"], "monthly": round(monthly_cost(account), 2),
                                  "status": account["status"]})
        target["monthly_total"] = round(target["monthly_total"] + monthly_cost(account), 2)
    ordered = sorted(groups.values(), key=lambda item: (item["funder"].startswith("Payment method"), -item["monthly_total"]))
    for item in ordered:
        open_charges = [charge for charge in item["charges"] if charge["status"] != "done"]
        if item["account_id"] and open_charges:
            item["warning"] = f"Stop {len(open_charges)} charge{'s' if len(open_charges) != 1 else ''} here before closing {item['funder'].split(' (')[0]}."
    return {"sources": ordered}


# ---- Exports: executor report and document packets ---------------------------------------------------

_STYLE = ("body{font-family:Georgia,serif;max-width:860px;margin:32px auto;padding:0 20px;color:#253025;line-height:1.55}"
          "h1{font-weight:400;font-size:30px}h2{font-weight:400;border-bottom:1px solid #d9ddd2;padding-bottom:4px;margin-top:30px}"
          "table{border-collapse:collapse;width:100%;font:13px/1.45 system-ui,sans-serif}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #e5e8df;vertical-align:top}"
          "th{background:#f2f4ee}.muted{color:#6b7466;font:12px system-ui,sans-serif}.box{background:#f6f7f2;border:1px solid #e1e5da;border-radius:8px;padding:12px 14px;font:13px/1.6 system-ui,sans-serif}"
          "pre{white-space:pre-wrap;font:13px/1.6 Georgia,serif}@media print{body{margin:0}}")


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _amount_text(account: dict[str, Any]) -> str:
    if account.get("amount") is None:
        return "—"
    suffix = {"monthly": " / month", "annual": " / year"}.get(account.get("frequency"), "")
    return _money(account["amount"]) + suffix


def report_html(estate: dict[str, Any], activity: list[dict[str, Any]], today: date, *, executor: str, emails: dict[str, dict]) -> str:
    persona, totals = estate["persona"], estate["totals"]
    accounts = [account for account in estate.get("accounts", []) if not is_dismissed(account)]
    dismissed = [account for account in estate.get("accounts", []) if is_dismissed(account)]
    titles = {"leaving": "Money leaving", "waiting": "Money waiting", "notify": "People to notify", "owed": "Money owed", "legacy": "Digital legacy"}
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Executor report · {_e(persona.get('name'))}</title><style>{_STYLE}</style></head><body>",
             f"<h1>Executor report: {_e(persona.get('name'))}</h1>",
             f"<p class='muted'>Prepared {today:%B %d, %Y} by Lastly for {_e(executor)}. Date of death {_e(persona.get('date_of_death'))}. "
             "Every account below is backed by an email or bank record unless the family added it. This report is not legal advice.</p>",
             "<div class='box'>"
             f"<strong>{totals.get('accounts', len(accounts))}</strong> accounts · <strong>{_money(totals.get('monthly_drain'))}</strong> a month still charging · "
             f"<strong>{_money(totals.get('assets_found'))}</strong> waiting to be claimed · <strong>{_money(totals.get('debts_found'))}</strong> owed · "
             f"{sum(1 for a in accounts if a['status'] == 'done')} done, {sum(1 for a in accounts if a['status'] == 'in_progress')} in progress</div>"]
    for bucket, title in titles.items():
        rows = [account for account in accounts if account["bucket"] == bucket]
        if not rows:
            continue
        parts.append(f"<h2>{title}</h2><table><tr><th>Institution</th><th>Amount</th><th>Status</th><th>Assigned</th><th>Latest outcome</th><th>Evidence</th></tr>")
        for account in rows:
            outcome = account.get("outcome") or {}
            outcome_text = _e(outcome.get("text") or "")
            if outcome.get("reference_number"):
                outcome_text += f"<br><span class='muted'>Reference {_e(outcome['reference_number'])}</span>"
            corrected = " <span class='muted'>(corrected by the family)</span>" if account.get("original") else ""
            source = "Added by the family" if "family" in account.get("sources", []) else f"{len(account.get('evidence_ids') or [])} record(s): {_e(', '.join(account.get('sources', [])))}"
            parts.append(f"<tr><td>{_e(account['institution'])}{corrected}</td><td>{_e(_amount_text(account))}</td><td>{_e(account['status'].replace('_', ' '))}</td>"
                         f"<td>{_e(account.get('assigned_to') or '—')}</td><td>{outcome_text or '—'}</td><td>{source}</td></tr>")
        parts.append("</table>")
    open_followups = [(account, item) for account in accounts for item in account.get("followups") or [] if not item.get("done")]
    parts.append("<h2>Outstanding follow-ups</h2>")
    if open_followups:
        parts.append("<table><tr><th>Due</th><th>Institution</th><th>Follow-up</th><th>Reference</th></tr>")
        for account, item in sorted(open_followups, key=lambda pair: pair[1].get("due") or "9999"):
            parts.append(f"<tr><td>{_e(item.get('due') or '—')}</td><td>{_e(account['institution'])}</td><td>{_e(item['title'])}</td><td>{_e(item.get('reference') or '—')}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p class='muted'>None.</p>")
    notes = [(account, note) for account in accounts for note in account.get("notes") or []]
    if notes:
        parts.append("<h2>Family notes and handoffs</h2><table><tr><th>When</th><th>Who</th><th>Institution</th><th>Note</th></tr>")
        for account, note in sorted(notes, key=lambda pair: pair[1].get("created_at", ""), reverse=True):
            label = {"handoff": "Handoff", "help": "Asked for help"}.get(note.get("kind"), "Note")
            parts.append(f"<tr><td>{_e(str(note.get('created_at', ''))[:10])}</td><td>{_e(note.get('author'))}</td><td>{_e(account['institution'])}</td><td><strong>{label}:</strong> {_e(note['text'])}</td></tr>")
        parts.append("</table>")
    if dismissed:
        parts.append("<h2>Dismissed by the family</h2><ul>" + "".join(f"<li>{_e(a['institution'])}: {_e((a.get('review') or {}).get('reason') or 'Not an account')}</li>" for a in dismissed) + "</ul>")
    parts.append("<h2>Recent family activity</h2><table><tr><th>When</th><th>Who</th><th>What</th></tr>")
    names = {account["id"]: account["institution"] for account in estate.get("accounts", [])}
    for row in activity[:60]:
        parts.append(f"<tr><td>{_e(str(row.get('created_at', ''))[:16].replace('T', ' '))}</td><td>{_e(row.get('actor'))}</td><td>{_e(describe(row.get('action', ''), names.get(row.get('account_id'), 'an account')))}</td></tr>")
    parts.append("</table></body></html>")
    return "".join(parts)


def describe(action: str, institution: str) -> str:
    kind, _, value = str(action).partition(":")
    return {
        "status": {"done": f"marked {institution} done", "in_progress": f"started on {institution}", "open": f"reopened {institution}"}.get(value, f"updated {institution}"),
        "assigned": f"unassigned {institution}" if value == "Unassigned" else f"assigned {institution} to {value}",
        "review": {"confirmed": f"confirmed {institution}", "dismissed": f"dismissed {institution} as not an account", "open": f"reopened review of {institution}"}.get(value, f"reviewed {institution}"),
        "corrected": f"corrected the details of {institution}",
        "added": f"added {institution}",
        "note": f"left a note on {institution}",
        "handoff": f"handed {institution} to {value}" if value else f"handed off {institution}",
        "help": f"asked for help with {institution}",
        "followup": f"{'completed' if value == 'done' else 'added'} a follow-up for {institution}",
        "document": f"updated documents for {institution}",
        "outcome": f"recorded {institution}'s answer: {value.replace('_', ' ')}",
        "call": f"had the AI voice agent contact {institution}",
        "claim": f"{value} a claim with {institution}",
    }.get(kind, f"{action.replace(':', ' ')} ({institution})")


def packet_html(account: dict[str, Any], persona: dict[str, Any], letter: str, evidence: list[dict], account_guide: dict[str, Any], *, executor: str) -> str:
    docs = "".join(f"<tr><td>{'☑' if item['status'] in {'ready', 'sent', 'not_needed'} else '☐'}</td><td>{_e(item['name'])}</td><td>{_e(item['status'].replace('_', ' '))}</td></tr>"
                   for item in account_guide["documents"])
    outcome = account.get("outcome") or {}
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{_e(account['institution'])} packet</title><style>{_STYLE}</style></head><body>",
             f"<h1>{_e(account['institution'])}: document packet</h1>",
             f"<p class='muted'>For {_e(persona.get('name'))} (died {_e(persona.get('date_of_death'))}). Prepared for {_e(executor)}. Review everything before sending.</p>",
             f"<div class='box'><strong>Goal:</strong> {_e(account_guide['goal'])}<br><strong>Contact:</strong> {_e(account_guide['contact']['label'])}"
             + (f" <span class='muted'>{_e(account_guide['contact']['url'])}</span>" if account_guide["contact"].get("url") else "")
             + (f"<br><strong>Reference:</strong> {_e(outcome['reference_number'])}" if outcome.get("reference_number") else "") + "</div>",
             "<h2>Checklist</h2><table><tr><th></th><th>Document</th><th>Status</th></tr>" + docs + "</table>",
             "<h2>Steps</h2><ol>" + "".join(f"<li>{_e(step)}</li>" for step in account_guide["steps"]) + "</ol>",
             "<h2>Draft letter</h2><div class='box'><pre>" + _e(letter) + "</pre></div>",
             "<h2>Supporting records</h2>"]
    if evidence:
        parts.append("<table><tr><th>Date</th><th>From</th><th>Subject</th><th>Excerpt</th></tr>")
        for item in evidence:
            parts.append(f"<tr><td>{_e(item.get('date'))}</td><td>{_e(item.get('from'))}</td><td>{_e(item.get('subject'))}</td><td>{_e(str(item.get('body', ''))[:280])}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p class='muted'>The family added this account; there are no discovered records.</p>")
    parts.append("</body></html>")
    return "".join(parts)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
