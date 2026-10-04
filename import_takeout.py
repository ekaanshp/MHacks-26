"""Import a Gmail Takeout mailbox into a private, local Lastly dataset."""
from __future__ import annotations

import argparse
import html
import mailbox
import re
from collections.abc import Iterable
from datetime import date
from email.message import Message
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from config import DATA_DIR, now_date
from secure_storage import StorageError, encryption_enabled, ensure_private_directory, write_json


def decode(value: str | None) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except (LookupError, UnicodeError):
        return value or ""


def body_text(message) -> str:
    plain: list[str] = []
    markup: list[str] = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            text = str(part.get_payload())
        else:
            try:
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
        (plain if content_type == "text/plain" else markup).append(text)
    if plain:
        return plain[0].strip()[:2000]
    if markup:
        text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", markup[0], flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", html.unescape(text)).strip()[:2000]
    return ""


def detect_owner(path: Path, *, sample: int = 500) -> str:
    """The mailbox owner's address: the most common Delivered-To in a Takeout export."""
    box = mailbox.mbox(str(path), create=False)
    counts: dict[str, int] = {}
    try:
        for index, message in enumerate(box):
            if index >= sample:
                break
            address = parseaddr(decode(message.get("Delivered-To")))[1].lower()
            if address:
                counts[address] = counts.get(address, 0) + 1
    finally:
        box.close()
    return max(counts, key=counts.get) if counts else ""


def sender_allowed(sender: str, domains: tuple[str, ...]) -> bool:
    """True when the sender's domain is one of `domains` or a subdomain of one (mail.openai.com)."""
    domain = sender.rpartition("@")[2]
    return any(domain == allowed or domain.endswith("." + allowed) for allowed in domains)


def cutoff_date(today: date, years: int) -> date:
    if years < 1:
        raise ValueError("--years must be at least 1.")
    return today.replace(year=today.year - years, day=min(today.day, 28)) if today.month == 2 else today.replace(year=today.year - years)


def import_mailbox(path: Path, *, own_address: str = "", years: int = 5, today: date | None = None,
                   only_from: tuple[str, ...] = ()) -> dict:
    box = mailbox.mbox(str(path), create=False)
    try:
        return build_inbox(box, own_address=own_address, years=years, today=today, only_from=only_from,
                           bio="Imported locally from a Google Takeout export.")
    finally:
        box.close()


def build_inbox(messages: Iterable[Message], *, own_address: str = "", years: int = 5, today: date | None = None,
                only_from: tuple[str, ...] = (), bio: str = "") -> dict:
    today = today or now_date()
    cutoff = cutoff_date(today, years)
    own_address = own_address.strip().lower()
    emails = []
    only_from = tuple(domain.strip().lower().lstrip("@") for domain in only_from if domain.strip())
    counts = {"total": 0, "kept": 0, "invalid_dates": 0, "spam_or_trash": 0, "sent": 0, "outside_range": 0}
    if only_from:
        counts["other_senders"] = 0
    for message in messages:
        counts["total"] += 1
        labels = {label.strip().lower() for label in (message.get("X-Gmail-Labels", "")).split(",")}
        if labels & {"spam", "trash", "\\spam", "\\trash"}:
            counts["spam_or_trash"] += 1
            continue
        sender = parseaddr(decode(message.get("From")))[1].lower()
        if own_address and sender == own_address:
            counts["sent"] += 1
            continue
        # Messages from other senders are skipped before their dates or bodies are read.
        if only_from and not sender_allowed(sender, only_from):
            counts["other_senders"] += 1
            continue
        try:
            message_date = parsedate_to_datetime(message.get("Date", "")).date()
        except (ValueError, TypeError, OverflowError):
            counts["invalid_dates"] += 1
            continue
        if not cutoff <= message_date <= today:
            counts["outside_range"] += 1
            continue
        emails.append({
            "id": f"msg_{len(emails):04d}",
            "from": sender,
            "subject": decode(message.get("Subject")),
            "date": message_date.isoformat(),
            "body": body_text(message),
        })
    counts["kept"] = len(emails)
    counts["senders"] = len({email["from"] for email in emails})
    dates = sorted(email["date"] for email in emails)
    counts["first_date"], counts["last_date"] = (dates[0], dates[-1]) if dates else (None, None)
    return {
        "persona": {"name": "", "email": own_address, "age": None, "city": "", "date_of_death": today.isoformat(), "bio": bio},
        "today": today.isoformat(),
        "synthetic": False,
        "emails": emails,
        "import_stats": counts,
    }


def save_inbox(result: dict, output: Path, *, name: str | None = None, executor: str = "Family") -> None:
    """Encrypt the inbox to `output`; with a name it becomes an estate the family can sign in to."""
    if name:
        result["imported"] = True
        result["persona"].update({"name": " ".join(name.split()), "executor": executor,
                                  "relatives": [{"name": executor, "relationship": "family", "executor": True}]})
    ensure_private_directory(output.parent)
    write_json(output, result, overwrite=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mbox", type=Path)
    parser.add_argument("--email", required=True, help="Owner's address, used to skip their outgoing emails.")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--output", type=Path, default=DATA_DIR / "inbox.json")
    parser.add_argument("--only-from", default="", help="Comma-separated sender domains to keep, e.g. openai.com,anthropic.com. Everything else is dropped.")
    parser.add_argument("--name", help="The estate's name. Makes the output a signed-in estate when saved under data/estates/<slug>/.")
    parser.add_argument("--executor", default="Family", help="First name of the family member who signs in (with --name).")
    args = parser.parse_args()
    try:
        if not encryption_enabled():
            parser.error("Set LASTLY_DATA_KEY to a random URL-safe base64 encoded 32-byte key before importing private mail.")
        result = import_mailbox(args.mbox, own_address=args.email, years=args.years, only_from=tuple(args.only_from.split(",")))
        save_inbox(result, args.output, name=args.name, executor=args.executor)
    except FileExistsError:
        parser.error("Output already exists. Choose a new private directory with --output; existing data is preserved.")
    except StorageError as exc:
        parser.error(str(exc))
    counts = result["import_stats"]
    print(f"Total messages: {counts['total']}; kept: {counts['kept']}; distinct senders: {counts['senders']}; invalid dates: {counts['invalid_dates']}")
    print(f"Saved locally to {args.output}. No data was sent to a cloud service.")


if __name__ == "__main__":
    main()
