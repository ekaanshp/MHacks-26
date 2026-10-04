# Lastly

**Tagline:** One shared place to find the accounts, stop the charges, and take the next step after a loss.

## Inspiration

A person's digital life can contain accounts their family has never seen. Subscriptions continue, benefits require a claim, and every company has its own process. We wanted to make that work easier to understand and divide, while preserving the evidence behind every finding.

## What it does

Lastly analyzes email and bank transactions, builds an estate ledger, separates recurring charges from assets, debts, notifications, and digital legacy, and identifies upcoming renewals. Family members assign tasks and track progress together. Evidence drawers show the email or bank transaction supporting each account. The app drafts letters for cancellation, transfer, claims, notification, and memorialization, with executor contact placeholders.

The synthetic Margaret demonstration includes a $50,000 MetLife life insurance policy, several accounts hidden in a single Apple receipt, a stopped Hulu subscription, a free trial about to bill, distinct Chase checking and credit card accounts, and a subscription visible only on a bank statement. Gmail Takeout imports offer a path for real inboxes with local processing by default.

## How we built it

We built a Python/FastAPI backend and a browser dashboard. The account-free mode uses local extraction rules and a JSON store. The Anthropic adapter performs structured account extraction, evidence-backed questions, and validated letter generation when enabled. Neon Postgres provides the shared Family Hub persistence adapter, including saved status, assignments, and activity history. ElevenLabs provides the outbound conversational voice adapter through a Twilio number. The Fetch.ai uAgents bridge implements Chat Protocol, forwards questions to Lastly's API, and returns email and bank evidence citations for Agentverse and ASI:One-compatible messaging.

**Current capability statement:** All adapters and offline functionality are implemented. Provider accounts are intentionally being set up later. Do not describe sponsor services as live, the voice call as demonstrated, or Agentverse registration as complete until the integration checks in `docs/integrations.md` pass. Replace this paragraph with the actual verified status before submission.

## Challenges

One sender can represent multiple accounts, receipts can contain multiple products, and old receipts can describe subscriptions that no longer bill. Bank merchant descriptors rarely match the email institution name. We preserve provenance while merging those sources and measure recall against an independent synthetic truth file. A call ending also does not mean a task succeeded: a document requirement keeps cancellation pending.

## Accomplishments

The product makes an estate discoverable and actionable in one flow: find an account, inspect the evidence, assign it, draft a request, and track the result. The deterministic mode makes the whole local flow available before API accounts exist. The coverage checklist reminds families to check sources the app does not possess, including credit reports, tax returns, insurance locators, and unclaimed property.

## Accuracy to report after verification

Run the current fixture generator and pipeline. Insert the actual measured account recall and extraction mode here. Quote both numerator and denominator, and state that the dataset is synthetic. Record separate live Anthropic results after that account is configured. Do not turn perfect fixture recall into a claim about unseen real inboxes or guaranteed asset discovery.

## What's next

Connect and verify sponsor accounts, evaluate consented real inboxes, improve issuer-specific bereavement workflows, and add provider-approved Gmail/Outlook authorization. Review access control and estate representative verification before hosting real family data. The existing Takeout workflow offers a local import path now.

## Submission assets

Attach a dashboard screenshot, an evidence screenshot, a phone-result screenshot only after a real role-play, and a demonstration video under two minutes. Use synthetic data. Add the actual repository link after the team chooses to commit and publish. Save the external Devpost draft, verify current competition rules, and submit at least one hour before the confirmed deadline.
