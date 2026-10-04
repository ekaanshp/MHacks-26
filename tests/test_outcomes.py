"""Call outcomes: a company's own clear, unconditional words are the only proof of completion."""
from __future__ import annotations

import pytest

import calls

CANCEL_QUESTION = "Just to confirm for the family: has the Netflix account been cancelled?"
OTHER_QUESTION = "Just to confirm for the family: has this request been completed, or is anything still needed?"


def outcome(*replies, action="cancel", question=CANCEL_QUESTION):
    transcript = [{"role": "agent", "message": question}]
    for reply in replies:
        transcript.append({"role": "user", "message": reply})
    return calls.summarize_transcript(transcript, action=action)


@pytest.mark.parametrize("reply", [
    "Yes, if you send the death certificate.",
    "Sure, as soon as the documents arrive.",
    "Yes, it will be cancelled once we receive the certificate.",
    "Yes. Actually no, sorry, it's still active.",
    "It has been cancelled. Wait, my mistake, it can't be cancelled without the executor.",
    "We'll cancel it tomorrow.",
    "Yes, we can cancel it after you email the paperwork.",
    "Correct, pending review by our bereavement team.",
    "The account has been cancelled, but it was reactivated this morning.",
    "No.",
])
def test_conditional_future_and_contradicted_replies_never_confirm(reply):
    summary = outcome(reply)
    assert summary["cancelled"] is False and summary["result"] != "completed"


@pytest.mark.parametrize("reply", [
    "Yes, it has been cancelled.",
    "Yep. Reference is NF-55821.",
    "I don't need any documents from you now. I've canceled the membership, and there'll be no further charges.",
    "The subscription was cancelled effective today.",
])
def test_unconditional_confirmations_count(reply):
    summary = outcome(reply)
    assert summary["cancelled"] is True and summary["result"] == "completed"


def test_a_later_correction_overrides_an_earlier_confirmation():
    assert outcome("Yes, it has been cancelled.", "Sorry, I misspoke. It is still active until the executor calls.")["result"] != "completed"


def test_a_later_clear_confirmation_overrides_an_earlier_condition():
    assert outcome("We need the death certificate first.", "Thanks, I received it. The membership has been cancelled.")["result"] == "completed"


def test_conditions_produce_documents_required_with_next_steps():
    summary = outcome("Yes, if you send the death certificate.")
    assert summary["result"] == "documents_required"
    assert summary["next_steps"] == ["Yes, if you send the death certificate."]


@pytest.mark.parametrize("action, reply, expected", [
    ("claim", "I've opened a claim, claim number CL-20391. We'll need a certified death certificate and the claim form.", "documents_required"),
    ("claim", "I've opened a claim for you. Your claim number is CL-20391.", "accepted"),
    ("claim", "The claim has been approved and the benefit has been paid.", "completed"),
    ("claim", "The claim will be paid once probate closes.", "unclear"),
    ("notify", "We have updated our records and benefits have been stopped.", "completed"),
    ("notify", "We will update the records next week.", "unclear"),
    ("transfer", "The balance has been transferred to the estate account.", "completed"),
    ("transfer", "I can't find an account under that name.", "declined"),
    ("memorialize", "The account has been memorialized.", "completed"),
    ("memorialize", "Your request has been received; please upload proof of death.", "documents_required"),
])
def test_each_action_has_its_own_outcome(action, reply, expected):
    summary = outcome(reply, action=action, question=OTHER_QUESTION)
    assert summary["result"] == expected
    assert summary["cancelled"] is False, "Only cancellation requests report cancelled"


def test_reference_numbers_and_promised_callbacks_are_kept():
    summary = outcome("Your case number is CS-7781. Someone will call you back within 5 business days.", action="notify", question=OTHER_QUESTION)
    assert summary["reference_number"] == "CS-7781"
    assert summary["callbacks"] and "call you back" in summary["callbacks"][0]


def test_unfinished_calls_are_never_completed():
    transcript = [{"role": "user", "message": "The membership has been cancelled."}]
    assert calls.summarize_transcript(transcript, completed=False)["result"] == "unclear"


@pytest.mark.parametrize("action", ["cancel", "claim", "transfer", "notify", "memorialize"])
def test_outcome_text_is_specific_to_the_action(action):
    text = calls.outcome_text({"result": "completed", "action": action})
    assert text.startswith("The company confirmed")
    assert calls.outcome_text({"result": "documents_required", "action": action}) == "The company needs documents before it can finish."
