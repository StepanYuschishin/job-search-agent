from __future__ import annotations

import os

import requests
from dotenv import load_dotenv


load_dotenv()


WHATSAPP_ACCESS_TOKEN = os.getenv(
    "WHATSAPP_ACCESS_TOKEN"
)

WHATSAPP_PHONE_NUMBER_ID = os.getenv(
    "WHATSAPP_PHONE_NUMBER_ID"
)

WHATSAPP_RECIPIENT_NUMBER = os.getenv(
    "WHATSAPP_RECIPIENT_NUMBER"
)

WHATSAPP_API_VERSION = os.getenv(
    "WHATSAPP_API_VERSION",
    "v26.0",
)


def _require_config() -> None:
    if not WHATSAPP_ACCESS_TOKEN:
        raise RuntimeError(
            "WHATSAPP_ACCESS_TOKEN is missing"
        )

    if not WHATSAPP_PHONE_NUMBER_ID:
        raise RuntimeError(
            "WHATSAPP_PHONE_NUMBER_ID is missing"
        )

    if not WHATSAPP_RECIPIENT_NUMBER:
        raise RuntimeError(
            "WHATSAPP_RECIPIENT_NUMBER is missing"
        )


def _send_whatsapp_payload(
    payload: dict,
) -> dict:
    _require_config()

    url = (
        f"https://graph.facebook.com/"
        f"{WHATSAPP_API_VERSION}/"
        f"{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )

    headers = {
        "Authorization":
            f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type":
            "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def send_text_message(
    text: str,
) -> dict:
    payload = {
        "messaging_product":
            "whatsapp",
        "recipient_type":
            "individual",
        "to":
            WHATSAPP_RECIPIENT_NUMBER,
        "type":
            "text",
        "text": {
            "preview_url":
                False,
            "body":
                text,
        },
    }

    return _send_whatsapp_payload(
        payload
    )


def send_hitl_notification(
    action: dict,
) -> dict:
    action_id = action.get(
        "id",
        "",
    )

    sender = action.get(
        "from",
        "",
    )

    subject = action.get(
        "subject",
        "",
    )

    classification = action.get(
        "classification",
        "",
    )

    classification_confidence = action.get(
        "confidence",
        0.0,
    )

    classification_reason = action.get(
        "reason",
        "",
    )

    recommended_action = action.get(
        "recommended_action",
        "REVIEW",
    )

    decision_confidence = action.get(
        "decision_confidence",
        0.0,
    )

    decision_reason = action.get(
        "decision_reason",
        "",
    )

    message = (
        "JOB SEARCH AGENT\n\n"
        "Human review required.\n\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n\n"
        f"Classification: {classification}\n"
        f"Classification confidence: "
        f"{classification_confidence:.0%}\n"
        f"Classification reason: "
        f"{classification_reason}\n\n"
        f"Recommended action: "
        f"{recommended_action}\n"
        f"Decision confidence: "
        f"{decision_confidence:.0%}\n"
        f"Decision reason: "
        f"{decision_reason}\n\n"
        "Choose:\n"
        "DRAFT\n"
        "IGNORE\n\n"
        f"Action ID:\n{action_id}"
    )

    return send_text_message(
        message
    )


def send_draft_for_approval(
    action: dict,
) -> dict:
    action_id = action.get(
        "id",
        "",
    )

    sender = action.get(
        "from",
        "",
    )

    subject = action.get(
        "subject",
        "",
    )

    draft = action.get(
        "draft",
        "",
    )

    draft_confidence = action.get(
        "draft_confidence",
        0.0,
    )

    needs_human_input = action.get(
        "draft_needs_human_input",
        False,
    )

    missing_information = action.get(
        "draft_missing_information",
        [],
    )

    draft_reason = action.get(
        "draft_reason",
        "",
    )

    if isinstance(
        missing_information,
        list,
    ):
        missing_information_text = (
            ", ".join(
                str(item)
                for item in missing_information
            )
            if missing_information
            else "None"
        )
    else:
        missing_information_text = str(
            missing_information
        )

    message = (
        "JOB SEARCH AGENT\n\n"
        "Draft ready for approval.\n\n"
        f"Original sender: {sender}\n"
        f"Subject: {subject}\n\n"
        "DRAFT\n"
        "-----\n"
        f"{draft}\n\n"
        "-----\n"
        f"Draft confidence: "
        f"{draft_confidence:.0%}\n"
        f"Needs human input: "
        f"{needs_human_input}\n"
        f"Missing information: "
        f"{missing_information_text}\n"
        f"Draft reason: "
        f"{draft_reason}\n\n"
        "Choose:\n"
        "SEND\n"
        "EDIT\n"
        "IGNORE\n\n"
        f"Action ID:\n{action_id}"
    )

    return send_text_message(
        message
    )


def send_test_whatsapp_message() -> dict:
    payload = {
        "messaging_product":
            "whatsapp",
        "to":
            WHATSAPP_RECIPIENT_NUMBER,
        "type":
            "template",
        "template": {
            "name":
                "hello_world",
            "language": {
                "code":
                    "en_US",
            },
        },
    }

    return _send_whatsapp_payload(
        payload
    )


if __name__ == "__main__":
    result = send_test_whatsapp_message()
    print(result)