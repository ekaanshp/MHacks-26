"""Local estate utilities and sponsor integration setup (Neon, ElevenLabs, Fetch.ai)."""
from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

import bank
import db
import integrations
from config import DATA_DIR
from secure_storage import encryption_enabled, read_json, read_text, write_json, write_text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    export = subcommands.add_parser("export", help="Save the current estate as an additional JSON backup.")
    export.add_argument("--output", type=Path, required=True)
    persona = subcommands.add_parser("persona", help="Update the connected inbox's name/date without exposing encrypted files.")
    persona.add_argument("--name")
    persona.add_argument("--date-of-death", type=date.fromisoformat)
    statement = subcommands.add_parser("import-bank", help="Validate and encrypt a bank CSV into the connected data directory.")
    statement.add_argument("--input", type=Path, required=True)
    statement.add_argument("--replace", action="store_true", help="Explicitly replace a previously imported statement.")
    subcommands.add_parser("reset-progress", help="Reset family edits in the synthetic estate before a rehearsal.")
    subcommands.add_parser("integrations", help="Check the Neon, ElevenLabs and Fetch.ai configuration and connectivity.")
    subcommands.add_parser("neon-init", help="Apply schema.sql to the Neon database in DATABASE_URL.")
    voice = subcommands.add_parser("elevenlabs-setup", help="Create or update the ElevenLabs calling agent; optionally import the Twilio number.")
    voice.add_argument("--twilio-number", help="E.164 Twilio number to import (uses TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN) and assign.")
    voice.add_argument("--voice-id", help="ElevenLabs voice ID; choose a calm, clear voice.")
    args = parser.parse_args()
    if args.command == "integrations":
        labels = {True: "OK  ", False: "FAIL", None: "OFF "}
        report = integrations.status_report()
        for name, ok, message in report:
            print(f"[{labels[ok]}] {name}: {message}")
        if any(ok is False for _, ok, _ in report):
            parser.exit(1)
        return
    if args.command in {"neon-init", "elevenlabs-setup"}:
        try:
            if args.command == "neon-init":
                print(integrations.neon_init())
            else:
                lines = integrations.elevenlabs_setup(voice_id=args.voice_id, twilio_number=args.twilio_number)
                print("ElevenLabs agent is configured with the AI disclosure and dynamic variables. Save in .env:")
                print("\n".join(lines))
        except integrations.SetupError as exc:
            parser.exit(1, f"{exc}\n")
        return
    directory = Path(os.getenv("LASTLY_DATA_DIR", str(DATA_DIR)))
    if args.command == "import-bank":
        if not encryption_enabled():
            parser.exit(1, "Bank imports require LASTLY_DATA_KEY.\n")
        rows = bank.read_rows(args.input)
        if not rows:
            parser.exit(1, "The bank statement must contain valid rows.\n")
        write_text(directory / "bank.csv", read_text(args.input), overwrite=args.replace)
        print(f"Imported {len(rows)} bank rows. Analyze the inbox again to refresh evidence.")
        return
    if args.command == "persona":
        if args.name is None and args.date_of_death is None:
            parser.error("Provide --name or --date-of-death.")
        inbox = read_json(directory / "inbox.json")
        if inbox.get("synthetic") is not True and not encryption_enabled():
            parser.exit(1, "Private persona edits require LASTLY_DATA_KEY.\n")
        if args.name is not None:
            name = args.name.strip()
            if not name or len(name) > 150 or any(ord(char) < 32 or ord(char) == 127 for char in name):
                parser.error("Provide a name of 1–150 characters without control characters.")
            inbox["persona"]["name"] = name
        if args.date_of_death is not None:
            inbox["persona"]["date_of_death"] = args.date_of_death.isoformat()
        write_json(directory / "inbox.json", inbox)
        print("Updated the connected persona. Analyze the inbox again to refresh the estate.")
        return
    estate = db.load_estate()
    if not estate:
        parser.exit(1, "No analyzed estate is available.\n")
    if args.command == "export":
        if (estate.get("analysis") or {}).get("synthetic") is not True and not encryption_enabled():
            parser.exit(1, "Private backups require LASTLY_DATA_KEY.\n")
        try:
            write_json(args.output, estate, overwrite=False)
        except FileExistsError:
            parser.exit(1, "That backup already exists. Choose a new output filename.\n")
        print(f"Exported {len(estate['accounts'])} accounts to {args.output}.")
        return
    if (estate.get("analysis") or {}).get("synthetic") is not True:
        parser.exit(1, "Reset is available only for the synthetic demo, so real family progress is preserved.\n")
    for account in estate["accounts"]:
        db.update_account(estate["estate_id"], account["id"], status="open", assigned_to=None)
    # Clear demo claims too, so the agent-to-agent claim can be shown again.
    claims_file = directory / "runtime_claims.json"
    if claims_file.exists():
        write_json(claims_file, {})
    print(f"Reset {len(estate['accounts'])} synthetic accounts to Open and unassigned, and cleared demo claims.")


if __name__ == "__main__":
    main()
