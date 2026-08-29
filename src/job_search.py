"""Core logic for the Job Search Agent.

The system reads recruiting emails from Gmail, classifies job-search activity
with an LLM, maintains persistent local state, identifies safe rejection emails
that may receive a predefined reply, and generates scheduled analytics
dashboards.

Autonomous write access is deliberately bounded. The agent may send only:
1. predefined replies to high-confidence, explicitly replyable rejection emails;
2. its own job-search dashboard.

It does not apply for jobs, negotiate, schedule interviews, or compose arbitrary
recruiter messages.
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from openai import (
    APIConnectionError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)



try:
    from .decision_router import decide_recruiter_action
    from .hitl import (
        create_pending_action,
        find_open_action,
        get_action,
        update_pending_action,
    )
    from .whatsapp import send_hitl_notification
except ImportError:
    from decision_router import decide_recruiter_action
    from hitl import (
        create_pending_action,
        find_open_action,
        get_action,
        update_pending_action,
    )
    from whatsapp import send_hitl_notification


# ---------------------------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"

CLASSIFICATION_CACHE_FILE = STATE_DIR / "job-search-classifications.json"
REJECTION_REPLIES_FILE = STATE_DIR / "rejection-replies.json"

OPENAI_MODEL = os.environ.get(
    "JOB_SEARCH_AGENT_CLASSIFIER_MODEL",
    "gpt-4.1-mini",
)

SELF_EMAIL = os.environ.get(
    "JOB_SEARCH_AGENT_SELF_EMAIL",
    "",
).strip().lower()

DEFAULT_START_DATE = os.environ.get(
    "JOB_SEARCH_AGENT_START_DATE",
    "2026-06-22",
)

REJECTION_CONFIDENCE_THRESHOLD = float(
    os.environ.get(
        "JOB_SEARCH_AGENT_REJECTION_CONFIDENCE",
        "0.95",
    )
)

MAX_REPLY_BATCH = int(
    os.environ.get(
        "JOB_SEARCH_AGENT_MAX_REPLY_BATCH",
        "20",
    )
)


# ---------------------------------------------------------------------------
# Safety policy
# ---------------------------------------------------------------------------

BLOCKED_SENDER_TERMS = [
    "no-reply",
    "noreply",
    "do-not-reply",
    "donotreply",
    "do_not_reply",
    "automated",
    "notification",
    "notifications",
    "system@",
    "support@",
    "mailer-daemon",
    "bounce",
    "successfactors",
    "myworkday",
    "do not reply",
    "workday@",
    "workday.hr@",
    "workflow.email.",
    "info@ing.com",
]

BLOCKED_SUBJECT_TERMS = [
    "termination of employment",
    "collective request",
    "separation measures",
    "message replied:",
    "relocation",
    "visa",
    "offboarding",
    "employment termination",
]

REJECTION_REASON_TERMS = [
    "will not proceed",
    "will not move forward",
    "not selected",
    "another candidate",
    "application was declined",
    "application declined",
    "application was unsuccessful",
    "application unsuccessful",
    "role was filled",
    "position was filled",
    "will not progress",
    "not progressing",
    "rejected",
    "rejection",
]

DEFAULT_REJECTION_REPLY = (
    "Thanks for letting me know. I appreciate the update and your time. "
    "Please feel free to keep me in mind for any relevant opportunities "
    "in the future."
)


# ---------------------------------------------------------------------------
# Generic state helpers
# ---------------------------------------------------------------------------

def _load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _save_json_file(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
    )


def _load_classification_cache() -> dict:
    return _load_json_file(CLASSIFICATION_CACHE_FILE)


def _save_classification_cache(cache: dict) -> None:
    _save_json_file(
        CLASSIFICATION_CACHE_FILE,
        cache,
    )


def _load_rejection_replies() -> dict:
    return _load_json_file(REJECTION_REPLIES_FILE)


def _save_rejection_replies(replies: dict) -> None:
    _save_json_file(
        REJECTION_REPLIES_FILE,
        replies,
    )


# ---------------------------------------------------------------------------
# Gmail integration
# ---------------------------------------------------------------------------

def _get_gmail_service():
    """Authenticate and return a Gmail API service."""
    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(
            TOKEN_FILE,
            GMAIL_SCOPES,
        )

    if (
        credentials
        and credentials.expired
        and credentials.refresh_token
    ):
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(
                f"Missing Gmail credentials file: {CREDENTIALS_FILE}"
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            CREDENTIALS_FILE,
            GMAIL_SCOPES,
        )

        credentials = flow.run_local_server(
            port=0,
        )

    TOKEN_FILE.write_text(
        credentials.to_json()
    )

    return build(
        "gmail",
        "v1",
        credentials=credentials,
    )


def _execute_gmail_request(
    request,
    attempts: int = 3,
    delay_seconds: int = 5,
):
    """Execute a Gmail API request with retry for temporary transport failures."""
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            return request.execute()

        except (
            TimeoutError,
            ConnectionError,
            ConnectionResetError,
        ) as error:
            last_error = error

            if attempt == attempts:
                raise

            time.sleep(
                delay_seconds * attempt
            )

    raise last_error


def _extract_gmail_text(payload: dict) -> str:
    """Extract readable text from a Gmail MIME payload."""
    mime_type = payload.get(
        "mimeType",
        "",
    )

    body_data = (
        payload
        .get("body", {})
        .get("data")
    )

    if body_data:
        try:
            decoded = base64.urlsafe_b64decode(
                body_data.encode("utf-8")
            ).decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            decoded = ""

        if mime_type == "text/plain":
            return decoded

        if mime_type == "text/html":
            text = re.sub(
                r"<script.*?</script>",
                " ",
                decoded,
                flags=re.S | re.I,
            )

            text = re.sub(
                r"<style.*?</style>",
                " ",
                text,
                flags=re.S | re.I,
            )

            text = re.sub(
                r"<[^>]+>",
                " ",
                text,
            )

            text = html.unescape(text)

            return re.sub(
                r"\s+",
                " ",
                text,
            ).strip()

    text_parts = []

    for part in payload.get(
        "parts",
        [],
    ):
        part_text = _extract_gmail_text(
            part
        )

        if part_text:
            text_parts.append(
                part_text
            )

    return "\n".join(
        text_parts
    )


def _get_gmail_message_summary(
    service,
    message_id: str,
) -> dict:
    """Read Gmail metadata and full body for one message."""
    message = _execute_gmail_request(
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="full",
        )
    )

    headers = {
        header["name"].lower():
            header["value"]
        for header in (
            message
            .get("payload", {})
            .get("headers", [])
        )
    }

    body = _extract_gmail_text(
        message.get(
            "payload",
            {},
        )
    ).strip()

    return {
        "id": message_id,
        "thread_id": message.get(
            "threadId",
            "",
        ),
        "message_header_id": headers.get(
            "message-id",
            "",
        ),
        "subject": headers.get(
            "subject",
            "",
        ),
        "from": headers.get(
            "from",
            "",
        ),
        "reply_to": headers.get(
            "reply-to",
            "",
        ),
        "date": headers.get(
            "date",
            "",
        ),
        "snippet": message.get(
            "snippet",
            "",
        ),
        "body": body,
    }


# ---------------------------------------------------------------------------
# Job-search candidate discovery
# ---------------------------------------------------------------------------

def _get_job_email_candidates_between(
    service,
    start_datetime: datetime,
    end_datetime: datetime,
) -> set[str]:
    """Retrieve a broad deduplicated candidate set of job-search emails."""
    start_timestamp = int(
        start_datetime.timestamp()
    )

    end_timestamp = int(
        end_datetime.timestamp()
    )

    candidate_queries = [
        f"after:{start_timestamp} before:{end_timestamp} application",
        f"after:{start_timestamp} before:{end_timestamp} candidate",
        f"after:{start_timestamp} before:{end_timestamp} recruiter",
        f"after:{start_timestamp} before:{end_timestamp} interview",
        f"after:{start_timestamp} before:{end_timestamp} hiring",
        f"after:{start_timestamp} before:{end_timestamp} position",
        f"after:{start_timestamp} before:{end_timestamp} role",
        f"after:{start_timestamp} before:{end_timestamp} unfortunately",
    ]

    message_ids: set[str] = set()

    for gmail_query in candidate_queries:
        page_token = None

        while True:
            response = _execute_gmail_request(
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=gmail_query,
                    pageToken=page_token,
                    maxResults=500,
                )
            )

            message_ids.update(
                message["id"]
                for message
                in response.get(
                    "messages",
                    [],
                )
            )

            page_token = response.get(
                "nextPageToken"
            )

            if not page_token:
                break

    return message_ids


# ---------------------------------------------------------------------------
# AI semantic classification
# ---------------------------------------------------------------------------

def _classify_job_email(
    email_data: dict,
) -> dict:
    """Classify one job-search email using the OpenAI API."""
    client = OpenAI(
        timeout=30.0,
        max_retries=0,
    )

    prompt = f"""
Classify this email into exactly one category.

Categories:
APPLICATION_CONFIRMATION
REJECTION
INTERVIEW
RECRUITER_REPLY
OTHER

Definitions:

APPLICATION_CONFIRMATION:
The email confirms or clearly acknowledges that a job application was
submitted, received, accepted into the recruiting system, or is under review.

REJECTION:
The email states that the candidate will not proceed, was not selected,
another candidate was chosen, the role was filled, the application was
unsuccessful, or the employer decided not to move forward.

INTERVIEW:
The email invites the candidate to an interview, assessment, screening call,
recruiter call, hiring-manager call, technical interview, or another hiring
stage.

RECRUITER_REPLY:
The email clearly concerns a specific job opportunity or application but does
not itself confirm submission, rejection, or an interview / next stage.

OTHER:
Anything unrelated to a specific job-search process, including generic job
alerts, newsletters, account verification, OTPs, surveys, marketing, or
administrative messages.

Important rules:
- Classify by meaning, not exact phrases.
- Prefer the most specific hiring-state category.
- "Thank you for applying" plus a decision not to proceed is REJECTION.
- Do not classify generic job alerts or newsletters as applications.
- Do not infer an application solely because the sender is a recruiter.
- Emails asking the candidate to complete, finish, resume, or continue an
  incomplete application are OTHER, not RECRUITER_REPLY and not
  APPLICATION_CONFIRMATION.
- Automated reminders about an unfinished application are OTHER unless the
  email separately confirms that an application was actually submitted.

Email:

From: {email_data.get("from", "")}
Subject: {email_data.get("subject", "")}
Snippet: {email_data.get("snippet", "")}

Full email body:
{email_data.get("body", "")}

Return JSON only:

{{
  "label": "APPLICATION_CONFIRMATION|REJECTION|INTERVIEW|RECRUITER_REPLY|OTHER",
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
                return json.loads(text)

            except json.JSONDecodeError:
                return {
                    "label": "OTHER",
                    "confidence": 0.0,
                    "reason": "classifier_invalid_json",
                }

        except (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
        ) as error:
            last_error = error

            if attempt == 3:
                raise

            time.sleep(
                5 * attempt
            )

    raise last_error


def _classify_job_search_emails_between(
    service,
    start_datetime: datetime,
    end_datetime: datetime,
) -> dict:
    """Return one classified snapshot for the selected period."""
    candidate_ids = _get_job_email_candidates_between(
        service,
        start_datetime,
        end_datetime,
    )

    counts = {
        "applications_submitted": 0,
        "rejections": 0,
        "interviews": 0,
        "recruiter_replies": 0,
        "other": 0,
    }

    classified_messages = []

    cache = _load_classification_cache()
    cache_changed = False

    for message_id in candidate_ids:
        cached_record = cache.get(
            message_id
        )

        if (
            cached_record
            and cached_record.get(
                "subject"
            ) is not None
        ):
            email_data = {
                "id": message_id,
                "thread_id": cached_record.get(
                    "thread_id",
                    "",
                ),
                "date": cached_record.get(
                    "date",
                    "",
                ),
                "subject": cached_record.get(
                    "subject",
                    "",
                ),
                "from": cached_record.get(
                    "from",
                    "",
                ),
                "reply_to": cached_record.get(
                    "reply_to",
                    "",
                ),
                "snippet": cached_record.get(
                    "snippet",
                    "",
                ),
                "body": "",
            }

            classification = {
                "label": cached_record.get(
                    "label",
                    "OTHER",
                ),
                "confidence": cached_record.get(
                    "confidence"
                ),
                "reason": cached_record.get(
                    "reason"
                ),
            }

        else:
            email_data = _get_gmail_message_summary(
                service,
                message_id,
            )

            classification = _classify_job_email(
                email_data
            )

            cache[message_id] = {
                "label": classification.get(
                    "label",
                    "OTHER",
                ),
                "confidence": classification.get(
                    "confidence"
                ),
                "reason": classification.get(
                    "reason"
                ),
                "thread_id": email_data.get(
                    "thread_id",
                    "",
                ),
                "date": email_data.get(
                    "date",
                    "",
                ),
                "subject": email_data.get(
                    "subject",
                    "",
                ),
                "from": email_data.get(
                    "from",
                    "",
                ),
                "reply_to": email_data.get(
                    "reply_to",
                    "",
                ),
                "snippet": email_data.get(
                    "snippet",
                    "",
                ),
            }

            _save_classification_cache(
                cache
            )

            cache_changed = True

        label = classification.get(
            "label",
            "OTHER",
        )

        if label == "APPLICATION_CONFIRMATION":
            counts["applications_submitted"] += 1

        elif label == "REJECTION":
            counts["rejections"] += 1

        elif label == "INTERVIEW":
            counts["interviews"] += 1

        elif label == "RECRUITER_REPLY":
            counts["recruiter_replies"] += 1

        else:
            counts["other"] += 1

        classified_messages.append(
            {
                "id": message_id,
                "thread_id": email_data.get(
                    "thread_id",
                    "",
                ),
                "date": email_data.get(
                    "date",
                    "",
                ),
                "subject": email_data.get(
                    "subject",
                    "",
                ),
                "from": email_data.get(
                    "from",
                    "",
                ),
                "reply_to": email_data.get(
                    "reply_to",
                    "",
                ),
                "label": label,
                "confidence": classification.get(
                    "confidence"
                ),
                "reason": classification.get(
                    "reason"
                ),
            }
        )

    if cache_changed:
        _save_classification_cache(
            cache
        )

    return {
        **counts,
        "candidate_messages": len(
            candidate_ids
        ),
        "classified_messages":
            classified_messages,
        "classification_method":
            "llm_semantic_with_local_cache",
    }



def send_one_recruiter_reply_to_hitl(
    start_date: str = DEFAULT_START_DATE,
    min_confidence: float = 0.75,
) -> dict:
    """Escalate at most one recruiter reply to WhatsApp for human review.

    This function never sends a Gmail reply.
    It creates persistent HITL state and sends only a WhatsApp notification.
    """
    abu_dhabi_timezone = timezone(
        timedelta(hours=4)
    )

    try:
        start_datetime = datetime.strptime(
            start_date,
            "%Y-%m-%d",
        ).replace(
            tzinfo=abu_dhabi_timezone
        )
    except ValueError:
        return {
            "error": "invalid_start_date",
            "expected_format": "YYYY-MM-DD",
        }

    now = datetime.now(
        abu_dhabi_timezone
    )

    service = _get_gmail_service()

    snapshot = _classify_job_search_emails_between(
        service,
        start_datetime,
        now,
    )

    candidates = []

    for message in snapshot.get(
        "classified_messages",
        [],
    ):
        if message.get("label") != "RECRUITER_REPLY":
            continue

        sender = str(
            message.get("from", "")
        ).lower()

        if (
            SELF_EMAIL
            and SELF_EMAIL in sender
        ):
            continue

        confidence = float(
            message.get("confidence")
            or 0.0
        )

        if confidence < min_confidence:
            continue

        if find_open_action(
            gmail_message_id=message.get(
                "id",
                "",
            ),
            gmail_thread_id=message.get(
                "thread_id",
                "",
            ),
        ):
            continue

        candidates.append(
            message
        )

    def message_timestamp(
        message: dict,
    ) -> float:
        raw_date = message.get(
            "date",
            "",
        )

        if not raw_date:
            return 0.0

        try:
            parsed = parsedate_to_datetime(
                raw_date
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(
                    tzinfo=timezone.utc
                )

            return parsed.timestamp()

        except (
            TypeError,
            ValueError,
            OverflowError,
        ):
            return 0.0

    candidates.sort(
        key=message_timestamp,
        reverse=True,
    )

    if not candidates:
        return {
            "status": "no_hitl_candidate",
            "min_confidence":
                min_confidence,
        }

    message = candidates[0]

    decision = decide_recruiter_action(
        message
    )

    recommended_action = decision.get(
        "recommended_action",
        "REVIEW",
    )

    action = create_pending_action(
        message,
        recommended_action=recommended_action,
        decision_confidence=decision.get(
            "confidence",
            0.0,
        ),
        decision_reason=decision.get(
            "reason",
            "",
        ),
    )

    update_pending_action(
        action_id=action["id"],
        status="PENDING_HUMAN",
        result={
            "whatsapp_notification":
                whatsapp_result,
        },
    )

    return {
        "status": "hitl_sent",
        "action_id":
            action["id"],
        "gmail_message_id":
            message.get("id"),
        "gmail_thread_id":
            message.get("thread_id"),
        "subject":
            message.get("subject"),
        "from":
            message.get("from"),
        "classification":
            message.get("label"),
        "confidence":
            message.get("confidence"),
        "recommended_action":
            recommended_action,
        "decision_confidence":
            decision.get(
                "confidence",
                0.0,
            ),
        "decision_reason":
            decision.get(
                "reason",
                "",
            ),
    }


# ---------------------------------------------------------------------------
# Rejection-reply decision layer
# ---------------------------------------------------------------------------

def _is_self_address(
    address: str,
) -> bool:
    if not SELF_EMAIL:
        return False

    return SELF_EMAIL in (
        address
        or ""
    ).lower()


def _rejection_has_semantic_evidence(
    reason: str,
) -> bool:
    reason = (
        reason
        or ""
    ).lower()

    return any(
        term in reason
        for term in REJECTION_REASON_TERMS
    )


def get_replyable_rejections(
    start_date: str = DEFAULT_START_DATE,
) -> dict:
    """Find high-confidence rejection emails that pass all reply guardrails."""
    local_timezone = timezone(
        timedelta(hours=4)
    )

    try:
        start_datetime = datetime.strptime(
            start_date,
            "%Y-%m-%d",
        ).replace(
            tzinfo=local_timezone
        )

    except ValueError:
        return {
            "error": "invalid_start_date",
            "expected_format": "YYYY-MM-DD",
        }

    now = datetime.now(
        local_timezone
    )

    service = _get_gmail_service()

    snapshot = _classify_job_search_emails_between(
        service,
        start_datetime,
        now,
    )

    already_replied = _load_rejection_replies()

    already_replied_thread_ids = {
        record.get(
            "thread_id"
        )
        for record in already_replied.values()
        if record.get(
            "thread_id"
        )
    }

    replyable = []
    skipped_no_reply = []
    skipped_already_replied = []

    for message in snapshot[
        "classified_messages"
    ]:
        if message.get(
            "label"
        ) != "REJECTION":
            continue

        message_id = message["id"]

        if message_id in already_replied:
            skipped_already_replied.append(
                message
            )
            continue

        thread_id = message.get(
            "thread_id"
        )

        if (
            thread_id
            and thread_id
            in already_replied_thread_ids
        ):
            skipped_already_replied.append(
                message
            )
            continue

        confidence = float(
            message.get(
                "confidence"
            )
            or 0
        )

        if confidence < REJECTION_CONFIDENCE_THRESHOLD:
            skipped_no_reply.append(
                message
            )
            continue

        if not _rejection_has_semantic_evidence(
            message.get(
                "reason",
                "",
            )
        ):
            skipped_no_reply.append(
                message
            )
            continue

        sender = (
            message
            .get("from", "")
            .lower()
        )

        reply_to = (
            message
            .get("reply_to", "")
            .lower()
        )

        subject = (
            message
            .get("subject", "")
            .lower()
        )

        effective_reply_address = (
            reply_to
            or sender
        )

        if _is_self_address(
            effective_reply_address
        ):
            skipped_no_reply.append(
                message
            )
            continue

        if _is_self_address(
            sender
        ):
            skipped_no_reply.append(
                message
            )
            continue

        if any(
            term in subject
            for term in BLOCKED_SUBJECT_TERMS
        ):
            skipped_no_reply.append(
                message
            )
            continue

        if any(
            term in effective_reply_address
            for term in BLOCKED_SENDER_TERMS
        ):
            skipped_no_reply.append(
                message
            )
            continue

        message[
            "effective_reply_address"
        ] = effective_reply_address

        replyable.append(
            message
        )

    return {
        "total_rejections":
            snapshot["rejections"],
        "replyable":
            replyable,
        "replyable_count":
            len(replyable),
        "skipped_no_reply_count":
            len(skipped_no_reply),
        "skipped_already_replied_count":
            len(skipped_already_replied),
        "skipped_no_reply":
            skipped_no_reply,
    }


def send_rejection_reply(
    message_id: str,
    body_text: str = DEFAULT_REJECTION_REPLY,
) -> dict:
    """Reply once to one safe rejection email inside its Gmail thread."""
    message_id = str(
        message_id
        or ""
    ).strip()

    if not message_id:
        return {
            "error": "message_id_required"
        }

    already_replied = _load_rejection_replies()

    if message_id in already_replied:
        return {
            "status": "skipped",
            "reason": "already_replied",
            "message_id": message_id,
        }

    service = _get_gmail_service()

    original = _get_gmail_message_summary(
        service,
        message_id,
    )

    thread_id = original.get(
        "thread_id",
        "",
    )

    already_replied_thread_ids = {
        record.get(
            "thread_id"
        )
        for record in already_replied.values()
        if record.get(
            "thread_id"
        )
    }

    if (
        thread_id
        and thread_id
        in already_replied_thread_ids
    ):
        return {
            "status": "skipped",
            "reason": "thread_already_replied",
            "thread_id": thread_id,
            "message_id": message_id,
        }

    cache = _load_classification_cache()

    classification = cache.get(
        message_id
    )

    if not classification:
        classification = _classify_job_email(
            original
        )

        cache[message_id] = {
            "label": classification.get(
                "label",
                "OTHER",
            ),
            "confidence": classification.get(
                "confidence"
            ),
            "reason": classification.get(
                "reason"
            ),
            "thread_id": original.get(
                "thread_id",
                "",
            ),
            "date": original.get(
                "date",
                "",
            ),
            "subject": original.get(
                "subject",
                "",
            ),
            "from": original.get(
                "from",
                "",
            ),
            "reply_to": original.get(
                "reply_to",
                "",
            ),
            "snippet": original.get(
                "snippet",
                "",
            ),
        }

        _save_classification_cache(
            cache
        )

    if classification.get(
        "label"
    ) != "REJECTION":
        return {
            "status": "skipped",
            "reason": "not_classified_as_rejection",
            "message_id": message_id,
        }

    confidence = float(
        classification.get(
            "confidence"
        )
        or 0
    )

    if confidence < REJECTION_CONFIDENCE_THRESHOLD:
        return {
            "status": "skipped",
            "reason": "low_rejection_confidence",
            "confidence": confidence,
            "message_id": message_id,
        }

    if not _rejection_has_semantic_evidence(
        classification.get(
            "reason",
            "",
        )
    ):
        return {
            "status": "skipped",
            "reason": "missing_rejection_evidence",
            "message_id": message_id,
        }

    sender = original.get(
        "from",
        "",
    )

    reply_to = original.get(
        "reply_to",
        "",
    )

    recipient = (
        reply_to
        or sender
    )

    recipient_lower = (
        recipient
        .lower()
    )

    subject = original.get(
        "subject",
        "",
    )

    subject_lower = (
        subject
        .lower()
    )

    if _is_self_address(
        sender
    ):
        return {
            "status": "skipped",
            "reason": "self_sender",
            "message_id": message_id,
        }

    if _is_self_address(
        recipient
    ):
        return {
            "status": "skipped",
            "reason": "self_recipient",
            "message_id": message_id,
        }

    if any(
        term in subject_lower
        for term in BLOCKED_SUBJECT_TERMS
    ):
        return {
            "status": "skipped",
            "reason": "blocked_subject",
            "subject": subject,
        }

    if any(
        term in recipient_lower
        for term in BLOCKED_SENDER_TERMS
    ):
        return {
            "status": "skipped",
            "reason": "blocked_recipient",
            "recipient": recipient,
        }

    if subject_lower.startswith(
        "re:"
    ):
        reply_subject = subject
    else:
        reply_subject = (
            f"Re: {subject}"
        )

    reply = EmailMessage()

    reply["To"] = recipient
    reply["From"] = "me"
    reply["Subject"] = reply_subject

    message_header_id = original.get(
        "message_header_id",
        "",
    )

    if message_header_id:
        reply["In-Reply-To"] = (
            message_header_id
        )
        reply["References"] = (
            message_header_id
        )

    reply.set_content(
        body_text
    )

    encoded_message = base64.urlsafe_b64encode(
        reply.as_bytes()
    ).decode(
        "utf-8"
    )

    sent = _execute_gmail_request(
        service.users()
        .messages()
        .send(
            userId="me",
            body={
                "raw": encoded_message,
                "threadId": thread_id,
            },
        )
    )

    local_timezone = timezone(
        timedelta(hours=4)
    )

    already_replied[
        message_id
    ] = {
        "sent_message_id": sent.get(
            "id"
        ),
        "thread_id": sent.get(
            "threadId"
        ),
        "recipient": recipient,
        "subject": reply_subject,
        "sent_at": datetime.now(
            local_timezone
        ).isoformat(),
    }

    _save_rejection_replies(
        already_replied
    )

    return {
        "status": "sent",
        "original_message_id": message_id,
        "sent_message_id": sent.get(
            "id"
        ),
        "thread_id": sent.get(
            "threadId"
        ),
        "recipient": recipient,
        "subject": reply_subject,
    }


def send_all_replyable_rejections(
    start_date: str = DEFAULT_START_DATE,
    max_batch: int = MAX_REPLY_BATCH,
) -> dict:
    """Send one predefined reply to each currently safe rejection."""
    discovery = get_replyable_rejections(
        start_date
    )

    if "error" in discovery:
        return discovery

    replyable = discovery[
        "replyable"
    ][:max_batch]

    results = []

    for message in replyable:
        result = send_rejection_reply(
            message["id"]
        )

        results.append(
            {
                "original_message_id":
                    message["id"],
                "recipient":
                    message.get(
                        "effective_reply_address"
                    ),
                "subject":
                    message.get(
                        "subject"
                    ),
                "result":
                    result,
            }
        )

    sent_count = sum(
        1
        for item in results
        if item[
            "result"
        ].get(
            "status"
        ) == "sent"
    )

    skipped_count = sum(
        1
        for item in results
        if item[
            "result"
        ].get(
            "status"
        ) == "skipped"
    )

    return {
        "status": "completed",
        "discovered_replyable":
            discovery["replyable_count"],
        "attempted":
            len(replyable),
        "sent":
            sent_count,
        "skipped":
            skipped_count,
        "max_batch":
            max_batch,
        "results":
            results,
    }




def send_approved_draft(
    action_id: str,
    execute: bool = False,
) -> dict:
    """Send one human-approved draft into its original Gmail thread.

    execute=False performs a dry run and never sends Gmail.
    """

    action = get_action(
        action_id
    )

    if not action:
        return {
            "status": "skipped",
            "reason": "unknown_action",
            "action_id": action_id,
        }

    if action.get(
        "status"
    ) != "SEND_APPROVED":
        return {
            "status": "skipped",
            "reason": "action_not_send_approved",
            "action_id": action_id,
            "action_status": action.get(
                "status"
            ),
        }

    draft = str(
        action.get(
            "draft",
            "",
        )
    ).strip()

    if not draft:
        return {
            "status": "skipped",
            "reason": "draft_missing",
            "action_id": action_id,
        }

    message_id = str(
        action.get(
            "gmail_message_id",
            "",
        )
    ).strip()

    if not message_id:
        return {
            "status": "skipped",
            "reason": "gmail_message_id_missing",
            "action_id": action_id,
        }

    service = _get_gmail_service()

    original = _get_gmail_message_summary(
        service,
        message_id,
    )

    sender = original.get(
        "from",
        "",
    )

    reply_to = original.get(
        "reply_to",
        "",
    )

    recipient = (
        reply_to
        or sender
    )

    if _is_self_address(
        sender
    ):
        return {
            "status": "skipped",
            "reason": "self_sender",
            "action_id": action_id,
        }

    if _is_self_address(
        recipient
    ):
        return {
            "status": "skipped",
            "reason": "self_recipient",
            "action_id": action_id,
        }

    recipient_lower = recipient.lower()

    if any(
        term in recipient_lower
        for term in BLOCKED_SENDER_TERMS
    ):
        return {
            "status": "skipped",
            "reason": "blocked_recipient",
            "recipient": recipient,
            "action_id": action_id,
        }

    subject = original.get(
        "subject",
        "",
    )

    if subject.lower().startswith(
        "re:"
    ):
        reply_subject = subject
    else:
        reply_subject = (
            f"Re: {subject}"
        )

    thread_id = original.get(
        "thread_id",
        "",
    )

    if not execute:
        return {
            "status": "dry_run",
            "action_id": action_id,
            "original_message_id": message_id,
            "thread_id": thread_id,
            "recipient": recipient,
            "subject": reply_subject,
            "draft": draft,
        }

    reply = EmailMessage()

    reply["To"] = recipient
    reply["From"] = "me"
    reply["Subject"] = reply_subject

    message_header_id = original.get(
        "message_header_id",
        "",
    )

    if message_header_id:
        reply["In-Reply-To"] = (
            message_header_id
        )
        reply["References"] = (
            message_header_id
        )

    reply.set_content(
        draft
    )

    encoded_message = base64.urlsafe_b64encode(
        reply.as_bytes()
    ).decode(
        "utf-8"
    )

    sent = _execute_gmail_request(
        service.users()
        .messages()
        .send(
            userId="me",
            body={
                "raw": encoded_message,
                "threadId": thread_id,
            },
        )
    )

    result = {
        "status": "sent",
        "action_id": action_id,
        "original_message_id": message_id,
        "sent_message_id": sent.get(
            "id"
        ),
        "thread_id": sent.get(
            "threadId"
        ),
        "recipient": recipient,
        "subject": reply_subject,
    }

    update_pending_action(
        action_id=action_id,
        status="COMPLETED",
        result=result,
    )

    return result


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def get_job_search_dashboard(
    start_date: str = DEFAULT_START_DATE,
) -> dict:
    """Build metrics from one classified Gmail snapshot."""
    local_timezone = timezone(
        timedelta(hours=4)
    )

    try:
        baseline_datetime = datetime.strptime(
            start_date,
            "%Y-%m-%d",
        ).replace(
            tzinfo=local_timezone
        )

    except ValueError:
        return {
            "error": "invalid_start_date",
            "expected_format": "YYYY-MM-DD",
        }

    now = datetime.now(
        local_timezone
    )

    today = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    yesterday = (
        today
        - timedelta(days=1)
    )

    seven_days_ago = (
        today
        - timedelta(days=7)
    )

    service = _get_gmail_service()

    snapshot = _classify_job_search_emails_between(
        service,
        baseline_datetime,
        now,
    )

    yesterday_counts = {
        "applications_submitted": 0,
        "rejections": 0,
        "interviews": 0,
        "recruiter_replies": 0,
        "other": 0,
    }

    last_7_days_counts = {
        "applications_submitted": 0,
        "rejections": 0,
        "interviews": 0,
        "recruiter_replies": 0,
        "other": 0,
    }

    label_to_key = {
        "APPLICATION_CONFIRMATION":
            "applications_submitted",
        "REJECTION":
            "rejections",
        "INTERVIEW":
            "interviews",
        "RECRUITER_REPLY":
            "recruiter_replies",
        "OTHER":
            "other",
    }

    for message in snapshot[
        "classified_messages"
    ]:
        raw_date = message.get(
            "date",
            "",
        )

        if not raw_date:
            continue

        try:
            message_datetime = (
                parsedate_to_datetime(
                    raw_date
                )
            )

            if message_datetime.tzinfo is None:
                message_datetime = (
                    message_datetime.replace(
                        tzinfo=timezone.utc
                    )
                )

            message_datetime = (
                message_datetime.astimezone(
                    local_timezone
                )
            )

        except (
            TypeError,
            ValueError,
            OverflowError,
        ):
            continue

        key = label_to_key.get(
            message.get(
                "label",
                "OTHER",
            ),
            "other",
        )

        if (
            yesterday
            <= message_datetime
            < today
        ):
            yesterday_counts[
                key
            ] += 1

        if (
            seven_days_ago
            <= message_datetime
            <= now
        ):
            last_7_days_counts[
                key
            ] += 1

    return {
        "dashboard":
            "Job Search Dashboard",
        "generated_at":
            now.isoformat(),
        "timezone":
            "Asia/Dubai",
        "since":
            start_date,
        "totals": {
            "applications_submitted":
                snapshot[
                    "applications_submitted"
                ],
            "rejections":
                snapshot[
                    "rejections"
                ],
            "interviews":
                snapshot[
                    "interviews"
                ],
            "recruiter_replies":
                snapshot[
                    "recruiter_replies"
                ],
        },
        "yesterday": {
            "date":
                yesterday.strftime(
                    "%Y-%m-%d"
                ),
            **yesterday_counts,
        },
        "last_7_days": {
            "from":
                seven_days_ago.strftime(
                    "%Y-%m-%d"
                ),
            "to":
                now.strftime(
                    "%Y-%m-%d"
                ),
            **last_7_days_counts,
        },
        "candidate_messages":
            snapshot[
                "candidate_messages"
            ],
        "access":
            "gmail_read_and_bounded_send",
        "classification_method":
            snapshot[
                "classification_method"
            ],
    }


def format_job_search_dashboard(
    start_date: str = DEFAULT_START_DATE,
    automation_stats: dict | None = None,
) -> str:
    """Return the job-search dashboard as readable plain text."""
    dashboard = get_job_search_dashboard(
        start_date
    )

    if "error" in dashboard:
        return json.dumps(
            dashboard,
            indent=2,
        )

    totals = dashboard[
        "totals"
    ]

    yesterday = dashboard[
        "yesterday"
    ]

    last_7_days = dashboard[
        "last_7_days"
    ]

    automation_stats = (
        automation_stats
        or {}
    )

    replyable_found = (
        automation_stats.get(
            "discovered_replyable",
            0,
        )
    )

    replies_sent = (
        automation_stats.get(
            "sent",
            0,
        )
    )

    total_rejection_replies = len(
        _load_rejection_replies()
    )

    return (
        "JOB SEARCH AGENT DASHBOARD\n"
        "==========================\n\n"

        f"Since: {dashboard['since']}\n"
        f"Applications submitted: "
        f"{totals['applications_submitted']}\n"
        f"Rejections: "
        f"{totals['rejections']}\n"
        f"Interview-stage emails: "
        f"{totals['interviews']}\n"
        f"Recruiter replies: "
        f"{totals['recruiter_replies']}\n"
        f"Rejection replies sent total: "
        f"{total_rejection_replies}\n\n"

        f"Yesterday ({yesterday['date']}):\n"
        f"+{yesterday['applications_submitted']} applications\n"
        f"+{yesterday['rejections']} rejections\n\n"

        f"Last 7 days "
        f"({last_7_days['from']} to {last_7_days['to']}):\n"
        f"{last_7_days['applications_submitted']} applications\n"
        f"{last_7_days['rejections']} rejections\n\n"

        "AUTOMATION\n"
        f"Replyable rejections found: "
        f"{replyable_found}\n"
        f"Rejection replies sent this run: "
        f"{replies_sent}\n\n"

        "SYSTEM\n"
        "Gmail access: Read + bounded Send\n"
        "Classification: AI semantic + local cache\n"
        "Duplicate protection: message + thread level"
    )


def email_job_search_dashboard(
    recipient: str,
    start_date: str = DEFAULT_START_DATE,
    automation_stats: dict | None = None,
) -> dict:
    """Generate and send the job-search dashboard."""
    recipient = str(
        recipient
        or ""
    ).strip()

    if (
        not recipient
        or "@"
        not in recipient
    ):
        return {
            "error": "valid_recipient_required"
        }

    dashboard_text = format_job_search_dashboard(
        start_date,
        automation_stats=automation_stats,
    )

    subject = (
        "Job Search Agent Dashboard"
    )

    message = EmailMessage()

    message["To"] = recipient
    message["From"] = "me"
    message["Subject"] = subject

    message.set_content(
        dashboard_text
    )

    encoded_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode(
        "utf-8"
    )

    service = _get_gmail_service()

    sent_message = _execute_gmail_request(
        service.users()
        .messages()
        .send(
            userId="me",
            body={
                "raw":
                    encoded_message
            },
        )
    )

    return {
        "status": "sent",
        "recipient": recipient,
        "subject": subject,
        "message_id":
            sent_message.get(
                "id"
            ),
        "access":
            "gmail_read_and_bounded_send",
    }