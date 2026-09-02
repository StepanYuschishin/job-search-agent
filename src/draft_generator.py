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
    "JOB_SEARCH_AGENT_DRAFT_MODEL",
    os.environ.get(
        "JOB_SEARCH_AGENT_CLASSIFIER_MODEL",
        "gpt-4.1-mini",
    ),
)


def generate_recruiter_reply_draft(
    email_data: dict,
    action: dict,
) -> dict:
    """Generate a proposed recruiter reply without sending anything."""

    client = OpenAI(
        timeout=30.0,
        max_retries=0,
    )

    prompt = f"""
You are drafting a reply for a candidate during an active job search.

Write a concise, professional, natural reply to the recruiter email below.

Important rules:

- Do not send anything. Return a proposed draft only.
- Do not invent facts, availability, salary expectations, experience,
  preferences, commitments, or answers that are not present in the context.
- If the recruiter asks for information that is not available, do not guess.
  Mention that the candidate should provide that information before sending.
- Keep the tone human and direct, not corporate or overly enthusiastic.
- Avoid generic AI-style phrases.
- Do not use "I hope this email finds you well."
- Preserve the conversation context.
- If the email does not clearly need a reply, produce a polite optional
  acknowledgement rather than inventing a reason to continue the conversation.
- The draft must be safe for human review. It will not be sent automatically.
- Never use placeholders such as "[Candidate Name]", "[Your Name]", or similar.
- Sign the draft with "Stepan" when a sign-off is appropriate.


Recruiter email:

From: {email_data.get("from", "")}
Subject: {email_data.get("subject", "")}

Body:
{email_data.get("body", "")}

Agent context:

Classification:
{action.get("classification", "")}

Classification reason:
{action.get("reason", "")}

Recommended action:
{action.get("recommended_action", "")}

Decision reason:
{action.get("decision_reason", "")}

Human decision:
{action.get("human_decision", "")}

Return JSON only:

{{
  "draft": "proposed email reply",
  "confidence": 0.0,
  "needs_human_input": false,
  "missing_information": [],
  "reason": "short explanation of why this draft is appropriate"
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
                result = json.loads(
                    text
                )
            except json.JSONDecodeError:
                return {
                    "draft": "",
                    "confidence": 0.0,
                    "needs_human_input": True,
                    "missing_information": [],
                    "reason": "draft_generator_invalid_json",
                }

            draft = str(
                result.get(
                    "draft",
                    "",
                )
            ).strip()

            try:
                confidence = float(
                    result.get(
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

            missing_information = result.get(
                "missing_information",
                [],
            )

            if not isinstance(
                missing_information,
                list,
            ):
                missing_information = []

            return {
                "draft":
                    draft,
                "confidence":
                    confidence,
                "needs_human_input":
                    bool(
                        result.get(
                            "needs_human_input",
                            False,
                        )
                    ),
                "missing_information":
                    missing_information,
                "reason":
                    str(
                        result.get(
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