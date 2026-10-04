"""A simulated insurer claims agent for demonstrating agent-to-agent estate claims.

This stands in for an insurance company's own agent. It is not affiliated with any
real insurer and never contacts one. Run it explicitly with `python insurer_agent.py`.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import date

from config import get_settings  # noqa: F401  (loads .env)

AGENT_NAME = "Simulated Insurer Claims Agent"
AGENT_DESCRIPTION = (
    "Demo stand-in for an insurance company's claims agent, used by Lastly to show agent-to-agent estate claims. "
    "Not affiliated with any real insurer. Accepts LastlyEstateClaim requests and replies with a claim number and required documents."
)
REQUIRED_DOCUMENTS = [
    "Certified copy of the death certificate",
    "Claimant's statement form, completed by the beneficiary",
    "Beneficiary's government-issued photo ID",
]


def claim_number(request_id: str) -> str:
    # Stable per request, so a redelivered message gets the same claim number.
    return "CLM-" + str(int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16) % 1_000_000).zfill(6)


def review(request) -> tuple[str, str | None, list[str], str]:
    """Decide the reply for one request: (status, claim number, documents, message)."""
    try:
        died = date.fromisoformat(request.date_of_death)
    except ValueError:
        return "rejected", None, [], "The date of death is not a valid date. Please resend the request."
    if not request.policyholder_name.strip() or len(request.policyholder_name) > 150 or died > date.today():
        return "rejected", None, [], "The policyholder's name or date of death is missing or invalid."
    if request.policy_type != "insurance":
        return "rejected", None, [], "This agent only opens life insurance claims."
    number = claim_number(request.request_id)
    return ("opened", number, list(REQUIRED_DOCUMENTS),
            f"Claim {number} is open for the policy of {request.policyholder_name} (date of death {died.isoformat()}). "
            "The named beneficiary must submit the documents below before the benefit can be paid. "
            "Simulated insurer: no real claim has been filed.")


def build_agent():
    try:
        from uagents import Agent, Context

        from agent_claims import ClaimRequest, ClaimResponse, LocalFirstResolver, claims_protocol
    except ImportError as exc:
        raise RuntimeError("Install the optional agent packages with pip install -r requirements-agent.txt.") from exc
    seed = os.getenv("CLAIMS_AGENT_SEED", "")
    if len(seed) < 32:
        raise ValueError("Set CLAIMS_AGENT_SEED to a long random phrase before starting the insurer agent.")
    port = int(os.getenv("CLAIMS_AGENT_PORT", "8002"))
    lastly_address = os.getenv("LASTLY_AGENT_ADDRESS", "")
    lastly_endpoint = os.getenv("LASTLY_AGENT_ENDPOINT", "")
    # Python 3.14 no longer creates an implicit event loop, which uAgents expects.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    agent = Agent(name=AGENT_NAME, seed=seed, port=port, endpoint=[f"http://127.0.0.1:{port}/submit"],
                  description=AGENT_DESCRIPTION, loop=loop,
                  resolve=LocalFirstResolver({lastly_address: lastly_endpoint}))
    protocol = claims_protocol()

    @protocol.on_message(ClaimRequest, replies={ClaimResponse})
    async def handle_claim(ctx: Context, sender: str, request: ClaimRequest):
        if not re.fullmatch(r"[a-f0-9]{32}", request.request_id):
            return
        status, number, documents, message = review(request)
        ctx.logger.info(f"Claim request {request.request_id[:8]} from {sender[:16]}…: {status} {number or ''}")
        await ctx.send(sender, ClaimResponse(request_id=request.request_id, status=status, claim_number=number,
                                             required_documents=documents, message=message))

    agent.include(protocol, publish_manifest=True)
    return agent


def main() -> None:
    try:
        agent = build_agent()
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Starting {AGENT_NAME}. Address: {agent.address}")
    print("This is a simulated insurer for demonstrations; it never files a real claim.")
    agent.run()


if __name__ == "__main__":
    main()
