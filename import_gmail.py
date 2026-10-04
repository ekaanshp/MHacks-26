"""Import mail from chosen senders straight from Gmail over IMAP into a private, local Lastly dataset.

Gmail runs the sender search itself, so messages from anyone else are never downloaded.
Sign in with an app password (myaccount.google.com/apppasswords), not your Google password.
"""
from __future__ import annotations

import argparse
import email
import getpass
import imaplib
import os
import re
from email import policy
from email.message import Message
from pathlib import Path

from config import now_date
from import_takeout import build_inbox, cutoff_date, save_inbox
from secure_storage import StorageError, encryption_enabled

HOST = "imap.gmail.com"


def all_mail_folder(client: imaplib.IMAP4_SSL) -> str:
    """Gmail's All Mail folder, found by its \\All flag because its name is localized."""
    status, folders = client.list()
    if status == "OK":
        for line in folders or []:
            text = line.decode(errors="replace")
            if "\\All" in text:
                match = re.search(r'"([^"]+)"\s*$', text) or re.search(r"(\S+)\s*$", text)
                if match:
                    return match.group(1)
    return "INBOX"


def gmail_query(domains: tuple[str, ...], after: str) -> str:
    senders = " OR ".join(f"from:{domain}" for domain in domains)
    return f"({senders}) after:{after}"


def fetch_messages(client: imaplib.IMAP4_SSL, domains: tuple[str, ...], after: str) -> list[Message]:
    folder = all_mail_folder(client)
    status, _ = client.select(f'"{folder}"', readonly=True)  # Read-only: nothing is marked read or changed.
    if status != "OK":
        raise RuntimeError(f"Could not open the {folder} folder.")
    status, data = client.uid("SEARCH", "X-GM-RAW", f'"{gmail_query(domains, after)}"')
    if status != "OK":
        raise RuntimeError("Gmail rejected the search.")
    messages = []
    for uid in (data[0] or b"").split():
        status, parts = client.uid("FETCH", uid, "(BODY.PEEK[])")
        raw = next((part[1] for part in parts or [] if isinstance(part, tuple)), None)
        if status == "OK" and raw:
            messages.append(email.message_from_bytes(raw, policy=policy.compat32))
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True, help="Your Gmail address.")
    parser.add_argument("--only-from", required=True, help="Comma-separated sender domains to import, e.g. openai.com,anthropic.com.")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--output", type=Path, help="Defaults to data/estates/<name>/inbox.json.")
    parser.add_argument("--name", required=True, help="The estate's name, used to sign in.")
    parser.add_argument("--executor", default="Family", help="First name of the family member who signs in.")
    args = parser.parse_args()
    domains = tuple(domain.strip().lower().lstrip("@") for domain in args.only_from.split(",") if domain.strip())
    if not domains:
        parser.error("--only-from needs at least one domain.")
    if not encryption_enabled():
        parser.error("Set LASTLY_DATA_KEY to a random URL-safe base64 encoded 32-byte key before importing private mail.")
    from config import root_data_dir, slugify
    output = args.output or root_data_dir() / "estates" / slugify(args.name) / "inbox.json"
    if output.exists():
        parser.error(f"{output} already exists. Delete that estate or choose another --output; existing data is preserved.")

    password = os.getenv("GMAIL_APP_PASSWORD") or getpass.getpass("Gmail app password (input hidden): ")
    today = now_date()
    try:
        with imaplib.IMAP4_SSL(HOST) as client:
            client.login(args.email, password.replace(" ", ""))
            messages = fetch_messages(client, domains, cutoff_date(today, args.years).strftime("%Y/%m/%d"))
    except imaplib.IMAP4.error as exc:
        parser.exit(1, f"Gmail sign-in failed ({exc}). Use an app password and check IMAP is enabled in Gmail settings.\n")
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"Could not read Gmail: {exc}\n")
    finally:
        password = ""

    # Gmail's from: also matches display names, so check each sender's domain again here.
    result = build_inbox(messages, own_address=args.email, years=args.years, today=today, only_from=domains,
                         bio="Imported directly from Gmail over IMAP.")
    if not result["emails"]:
        parser.exit(1, f"No messages from {', '.join(domains)} in the last {args.years} years.\n")
    try:
        save_inbox(result, output, name=args.name, executor=args.executor)
    except (FileExistsError, StorageError) as exc:
        parser.exit(1, f"{exc}\n")
    counts = result["import_stats"]
    print(f"Fetched {counts['total']} messages from Gmail; kept {counts['kept']} from {counts['senders']} senders "
          f"({counts['first_date']} to {counts['last_date']}).")
    print(f"Saved encrypted to {output}. Restart the server and sign in as \"{result['persona']['name']}\".")


if __name__ == "__main__":
    main()
