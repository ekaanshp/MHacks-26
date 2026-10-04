"""Five deceased people: exact sign-in, separate estates, per-person relatives and agents across estates."""
from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

import claims
import generate_people
import server

INSURER = "agent1q" + "a" * 58


@pytest.fixture
def people(dataset, monkeypatch):
    monkeypatch.setenv("LASTLY_EXTRA_ESTATES", "true")
    with TestClient(server.app, base_url="http://localhost", headers={"X-Requested-With": "Lastly"}) as client:
        yield client


def identify(client, deceased, name, relationship):
    return client.post("/api/identify", json={"deceased": deceased, "name": name, "relationship": relationship})


def test_every_generated_person_is_complete_and_distinct():
    names = [person["name"] for person in generate_people.PEOPLE]
    assert len(names) == 4 and len(set(names)) == 4
    for person in generate_people.PEOPLE:
        inbox, truth, rows = generate_people.build(person, "2026-10-03")
        assert inbox["persona"]["name"] == person["name"] and len(person["relatives"]) == 2
        assert sum(relative["executor"] for relative in person["relatives"]) == 1
        text = " ".join(email["subject"] + email["body"] for email in inbox["emails"])
        assert "Margaret" not in text and "Daniel Ellis" not in text
        assert not {account["institution"] for account in truth["accounts"]} & person["drop"]
        assert len(inbox["emails"]) == 790 and rows and all(row["id"].startswith("bank_") for row in rows)


def test_sign_in_requires_exact_names(people):
    ok = identify(people, "Harold Bennett", "Linda", "daughter")
    assert ok.status_code == 200 and ok.json()["found"] is True and ok.json()["estate"] == "harold-bennett" and ok.json()["pronoun"] == "he"
    # Relationship ignores capital letters; names do not.
    assert identify(people, "Harold Bennett", "Linda", "Daughter").status_code == 200
    for wrong in (("harold bennett", "Linda", "daughter"), ("Harold Bennet", "Linda", "daughter"),
                  ("Harold Bennett", "linda", "daughter"), ("Harold Bennett", "Linda", "son"),
                  ("Harold Bennett", "Sarah", "daughter")):
        response = identify(people, *wrong).json()
        assert response["found"] is False and "estate" not in response and "capital letters" in response["message"]
    assert identify(people, "Margaret Ellis", "Sarah", "daughter").json()["estate"] == "margaret-ellis"
    assert identify(people, "Eleanor Whitfield", "Anne", "granddaughter").json()["estate"] == "eleanor-whitfield"


def test_each_estate_is_separate_with_its_own_relatives(people):
    harold = {"X-Lastly-Estate": "harold-bennett", "X-Lastly-Actor": "Michael"}
    estate = people.get("/api/estate", headers=harold).json()
    assert estate["persona"]["name"] == "Harold Bennett"
    assert "Planet Fitness" not in {account["institution"] for account in estate["accounts"]}
    family = people.get("/api/family", headers=harold).json()
    assert [(p["name"], p["relationship"]) for p in family["relatives"]] == [("Linda", "daughter"), ("Michael", "son")]
    assert family["executor"] == "Linda"
    netflix = next(a for a in estate["accounts"] if a["institution"] == "Netflix")
    people.patch(f"/api/account/{netflix['id']}", json={"status": "done"}, headers=harold)
    assert people.get("/api/activity", headers=harold).json()["activity"][0]["actor"] == "Michael"
    # Margaret's estate is untouched by Harold's family.
    margaret = people.get("/api/estate").json()
    assert margaret["persona"]["name"] == "Margaret Ellis"
    assert next(a for a in margaret["accounts"] if a["institution"] == "Netflix")["status"] == "open"
    assert people.get("/api/activity").json()["activity"] == []
    assert people.get("/api/estate", headers={"X-Lastly-Estate": "nobody"}).status_code == 404


def test_agents_relay_claims_for_every_estate(people, monkeypatch):
    agent_token = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_AGENT_TOKEN", agent_token)
    monkeypatch.setenv("CLAIMS_AGENT_ADDRESS", INSURER)
    rosa = {"X-Lastly-Estate": "rosa-martinez"}
    metlife = next(a["id"] for a in people.get("/api/estate", headers=rosa).json()["accounts"] if a["institution"] == "MetLife")
    claim = people.post(f"/api/claim/{metlife}", headers=rosa).json()
    agent = {"Authorization": f"Bearer {agent_token}", "X-Lastly-Agent-Mode": "synthetic", "X-Lastly-Agent-Sender": "self"}
    pending = people.get("/api/agent/claims", headers=agent).json()["claims"]
    assert [(item["request_id"], item["policyholder_name"], item["claimant_name"]) for item in pending] == [(claim["id"], "Rosa Martinez", "Elena")]
    opened = {"status": "opened", "responder": INSURER, "claim_number": "CLM-000003", "required_documents": [], "message": "ok"}
    # The agent does not say which estate; the claim id finds it.
    assert people.post(f"/api/agent/claims/{claim['id']}", json=opened, headers=agent).status_code == 200
    rosa_metlife = next(a for a in people.get("/api/estate", headers=rosa).json()["accounts"] if a["institution"] == "MetLife")
    assert rosa_metlife["status"] == "in_progress"
    assert next(a for a in people.get("/api/estate").json()["accounts"] if a["institution"] == "MetLife")["status"] == "open"
    assert claims.insurer_address() == INSURER
