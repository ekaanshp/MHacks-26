# Two-minute demonstration

Prepare a cached estate before presenting: run `python generate_inbox.py`, then `python pipeline.py --mock` for the account-free rehearsal. For a live Anthropic demonstration, configure the account first, run `python pipeline.py`, and save its measured recall. Start the server and use `http://localhost:8000/?demo=1`. Refresh once to confirm the full click path is ready.

The synthetic timeline is generated relative to the current date in America/New_York. Use the dashboard's displayed dates and current renewal amounts in the narration. Do not quote stale September dates from the original plan. Evidence is synthetic; describe it that way when presenting.

| Time | Action | Narration |
|---|---|---|
| 0:00–0:15 | Show the start screen; begin inbox analysis. | “When a family loses someone, the accounts keep going. Lastly turns the paperwork into a shared plan.” |
| 0:15–0:35 | Show the dashboard's charges and urgent renewals. | “Here is what is still charging, what renews soon, and which accounts someone needs to handle.” |
| 0:35–0:55 | Open MetLife and its evidence email. | “In Margaret's synthetic inbox, we found a $50,000 life insurance policy. The family can inspect exactly where that finding came from.” |
| 0:55–1:10 | Open the bank-only yoga subscription. | “Email alone would miss this charge. A recurring bank transaction reveals it.” |
| 1:10–1:25 | Assign an account to Sarah and show a draft letter. | “The family divides the work. Lastly drafts a letter with the correct name and date, ready for the executor to review.” |
| 1:25–1:55 | Place a previously configured call to the teammate's phone on speaker. | “With the family's approval, an AI assistant calls the company. It introduces itself as AI and asks for the bereavement process.” |
| 1:55–2:00 | Show the confirmed result or pending steps. | “The family sees what the company actually confirmed and what remains to do.” |

If accounts have not yet been connected, rehearse the product flow and show the integration setup message at the phone step. Once configured, the call is the only live provider request required during the cached demo. Do not click Fresh analysis on stage. If a provider fails, use a previously recorded successful demonstration and explain that it is a recording. Keep the account in progress when the company requires documents before cancellation.

## Rehearsal and backup recording

Use the exact presentation laptop, browser, phone, and microphone. Complete five full rehearsals and check the 1280px/1440px projector sizes and 390px phone layout. Ensure the speakerphone is audible, trial notices do not surprise the presenter, and Escape closes the account drawer.

Once the phone integration works, screen-record the full demonstration with the phone audio. Keep it under two minutes, replay the recording to check sound, save a local copy on two laptops, and upload it to the eventual Devpost draft manually. Use synthetic evidence in all public screenshots and recordings. Take a dashboard screenshot, a MetLife evidence screenshot, and a confirmed-call or pending-steps screenshot.

Recording, sponsor account setup, submission, and on-stage rehearsal are human tasks. This repository includes their scripts and checks; it does not manufacture recordings or claim that uploads occurred.
