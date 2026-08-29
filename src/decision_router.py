from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)


BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(
    BASE_DIR / ".env"
)


OPENAI_MODEL = os.environ.get(
    "JOB_SEARCH_AGENT_CLASSIFIER_MODEL",
    "gpt-4.1-mini",
)

ALLOWED_ACTIONS = {
    "DRAFT",
    "REVIEW",
    "IGNORE",
    "AUTO",
}


def decide_recruiter_action(
    message: dict,
) -> dict:
    """Decide what should happen after a recruiter-related email."""

    client = OpenAI(
        timeout=30.0,
        max_retries=0,
    )

    prompt = f"""
You are the decision layer of a job-search email agent.

The email has already been classified as recruiter-related.

Decide what should happen next.

Allowed actions:

DRAFT
The email clearly requires or strongly benefits from a human response.
Examples:
- recruiter asks a question;
- recruiter requests availability;
- recruiter asks for information;
- recruiter asks the candidate to confirm something;
- recruiter starts a conversation about a specific opportunity.

REVIEW
Human judgment is useful, but it is unclear whether a response is needed.
Examples:
- detailed interview feedback;
- ambiguous recruiter update;
- informational message where replying may be polite but is not necessary.

IGNORE
No response is needed.
Examples:
- purely informational recruiter update;
- automated reminder;
- generic recruiting information;
- message that does not require any candidate action.

AUTO
The action is safe, deterministic, and explicitly supported by an existing
automation policy.

Important rules:

- Do not recommend DRAFT merely because the sender is a recruiter.
- Ask whether replying would materially advance or appropriately close the
  conversation.
- Informational messages normally do not require DRAFT.
- When uncertain between DRAFT and IGNORE, choose REVIEW.
- AUTO must be used only when the message clearly matches an already defined
  autonomous policy. Do not invent new autonomous actions.
- Be conservative with external communication.

Email:

From: {message.get("from", "")}
Subject: {message.get("subject", "")}
Snippet: {message.get("snippet", "")}

Classification:
{message.get("label", "")}

Classification confidence:
{message.get("confidence", 0.0)}

Classification reason:
{message.get("reason", "")}

Return JSON only:

{{
  "recommended_action": "DRAFT|REVIEW|IGNORE|AUTO",
  "confidence": 0.0,
  "reason": "short explanation"
}}
"""

    last_error = None

    for attempt in range(1, 4):
        try:
            response = client.responses.create(
                model=OPENAI_MODEL,
                input=prompt,
            )

            text = (
                response
                .output_text
                .strip()
            )

            try:
                decision = json.loads(
                    text
                )
            except json.JSONDecodeError:
                return {
                    "recommended_action":
                        "REVIEW",
                    "confidence":
                        0.0,
                    "reason":
                        "decision_router_invalid_json",
                }

            action = str(
                decision.get(
                    "recommended_action",
                    "REVIEW",
                )
            ).upper()

            if action not in ALLOWED_ACTIONS:
                action = "REVIEW"

            try:
                confidence = float(
                    decision.get(
                        "confidence",
                        0.0,
                    )
                )
            except (
                TypeError,
                ValueError,
            ):
                confidence = 0.0

            confidence = max(
                0.0,
                min(
                    confidence,
                    1.0,
                ),
            )

            return {
                "recommended_action":
                    action,
                "confidence":
                    confidence,
                "reason":
                    str(
                        decision.get(
                            "reason",
                            "",
                        )
                    ),
            }

        except (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
        ) as error:
            last_error = error

            if attempt == 3:
                raise

    raise last_error