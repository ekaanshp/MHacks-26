"""uAgents message models shared by Lastly's agent and the simulated insurer agent.

Imported only by the optional agent processes, never by the web server.
"""
from __future__ import annotations

from uagents import Model, Protocol
from uagents.resolver import GlobalResolver, Resolver, RulesBasedResolver

PROTOCOL_NAME = "LastlyEstateClaim"
PROTOCOL_VERSION = "1.0.0"


class ClaimRequest(Model):
    request_id: str
    policyholder_name: str
    date_of_death: str
    institution: str
    policy_type: str
    claimant_name: str


class ClaimResponse(Model):
    request_id: str
    status: str  # "opened" or "rejected"
    claim_number: str | None = None
    required_documents: list[str] = []
    message: str = ""


def claims_protocol() -> Protocol:
    return Protocol(name=PROTOCOL_NAME, version=PROTOCOL_VERSION)


class AccountTaskRequest(Model):
    request_id: str
    institution: str
    action: str
    category: str
    stage: str = "intro"
    person_name: str = ""
    date_of_death: str = ""
    executor_name: str = ""
    message: str = ""


class AccountTaskResponse(Model):
    request_id: str
    status: str
    message: str
    reference_number: str | None = None
    required_documents: list[str] = []


def tasks_protocol() -> Protocol:
    return Protocol(name="LastlyAccountAction", version="1.0.0")


class LocalFirstResolver(Resolver):
    """Use a known local endpoint for a peer agent; otherwise resolve through the Almanac."""

    def __init__(self, rules: dict[str, str]):
        self._rules = RulesBasedResolver({key: value for key, value in rules.items() if key and value})
        self._global = GlobalResolver()

    async def resolve(self, destination: str) -> tuple[str | None, list[str]]:
        address, endpoints = await self._rules.resolve(destination)
        if endpoints:
            return address, endpoints
        return await self._global.resolve(destination)
