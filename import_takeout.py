"""Import a Gmail Takeout mailbox into a private, local Lastly dataset."""
from __future__ import annotations

import argparse
import html
import mailbox
import re
from datetime import date
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from config import DATA_DIR, now_date
from secure_storage import StorageError, encryption_enabled, write_json


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


def import_mailbox(path: Path, *, own_address: str = "", years: int = 5, today: date | None = None) -> dict:
    today = today or now_date()
    if years < 1:
        raise ValueError("--years must be at least 1.")
    cutoff = today.replace(year=today.year - years, day=min(today.day, 28)) if today.month == 2 else today.replace(year=today.year - years)
    own_address = own_address.strip().lower()
    box = mailbox.mbox(str(path), create=False)
    emails = []
    counts = {"total": 0, "kept": 0, "invalid_dates": 0}
    try:
        for message in box:
            counts["total"] += 1
            labels = {label.strip().lower() for label in (message.get("X-Gmail-Labels", "")).split(",")}
            if labels & {"spam", "trash", "\\spam", "\\trash"}:
                continue
            sender = parseaddr(decode(message.get("From")))[1].lower()
            if own_address and sender == own_address:
                continue
            try:
                message_date = parsedate_to_datetime(message.get("Date", "")).date()
            except (ValueError, TypeError, OverflowError):
                counts["invalid_dates"] += 1
                continue
            if not cutoff <= message_date <= today:
                continue
            emails.append({
                "id": f"msg_{len(emails):04d}",
                "from": sender,
                "subject": decode(message.get("Subject")),
                "date": message_date.isoformat(),
                "body": body_text(message),
            })
    finally:
        box.close()
    counts["kept"] = len(emails)
    counts["senders"] = len({email["from"] for email in emails})
    return {
        "persona": {"name": "", "email": own_address, "age": None, "city": "", "date_of_death": today.isoformat(), "bio": "Imported locally from a Google Takeout export."},
        "today": today.isoformat(),
        "synthetic": False,
        "emails": emails,
        "import_stats": counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mbox", type=Path)
    parser.add_argument("--email", required=True, help="Owner's address, used to skip their outgoing emails.")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--output", type=Path, default=DATA_DIR / "inbox.json")
    args = parser.parse_args()
    try:
        if not encryption_enabled():
            parser.error("Set LASTLY_DATA_KEY to a random URL-safe base64 encoded 32-byte key before importing private mail.")
        result = import_mailbox(args.mbox, own_address=args.email, years=args.years)
        write_json(args.output, result, overwrite=False)
    except FileExistsError:
        parser.error("Output already exists. Choose a new private directory with --output; existing data is preserved.")
    except StorageError as exc:
        parser.error(str(exc))
    counts = result["import_stats"]
    print(f"Total messages: {counts['total']}; kept: {counts['kept']}; distinct senders: {counts['senders']}; invalid dates: {counts['invalid_dates']}")
    print(f"Saved locally to {args.output}. No data was sent to a cloud service.")


if __name__ == "__main__":
    main()
