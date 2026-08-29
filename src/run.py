"""Scheduled entry point for the Job Search Agent."""

from __future__ import annotations

import os
import signal
import sys
from datetime import datetime, timedelta, timezone

from draft_generator import generate_recruiter_reply_draft
from hitl import (
    _load_pending_actions,
    save_generated_draft,
    update_pending_action,
)
from job_search import (
    DEFAULT_START_DATE,
    MAX_REPLY_BATCH,
    _get_gmail_message_summary,
    _get_gmail_service,
    email_job_search_dashboard,
    send_all_replyable_rejections,
    send_approved_draft,
    send_one_recruiter_reply_to_hitl,
)
from whatsapp import send_draft_for_approval


DASHBOARD_RECIPIENT = os.environ.get(
    "JOB_SEARCH_AGENT_DASHBOARD_RECIPIENT",
    "",
).strip()

JOB_SEARCH_START_DATE = os.environ.get(
    "JOB_SEARCH_AGENT_START_DATE",
    DEFAULT_START_DATE,
)

MAX_RUN_SECONDS = int(
    os.environ.get(
        "JOB_SEARCH_AGENT_MAX_RUN_SECONDS",
        "600",
    )
)

HITL_LOOKBACK_DAYS = int(
    os.environ.get(
        "JOB_SEARCH_AGENT_HITL_LOOKBACK_DAYS",
        "2",
    )
)

MAX_HITL_BATCH = int(
    os.environ.get(
        "JOB_SEARCH_AGENT_MAX_HITL_BATCH",
        "5",
    )
)


class JobSearchRunTimeout(Exception):
    """Raised when a scheduled run exceeds its runtime ceiling."""


def _handle_timeout(
    signum,
    frame,
):
    raise JobSearchRunTimeout(
        f"Job Search Agent run exceeded "
        f"{MAX_RUN_SECONDS} seconds"
    )


def _actions_with_status(
    status: str,
    limit: int = MAX_HITL_BATCH,
) -> list[dict]:
    actions = _load_pending_actions()

    matching = [
        action
        for action in actions.values()
        if action.get("status") == status
    ]

    matching.sort(
        key=lambda action: action.get(
            "updated_at",
            action.get(
                "created_at",
                "",
            ),
        ),
    )

    return matching[:limit]


def _process_draft_requests() -> dict:
    actions = _actions_with_status(
        "DRAFT_REQUESTED"
    )

    if not actions:
        return {
            "status": "completed",
            "found": 0,
            "processed": 0,
            "failed": 0,
            "results": [],
        }

    service = _get_gmail_service()

    results = []
    processed = 0
    failed = 0

    for action in actions:
        action_id = action.get(
            "id",
            "",
        )

        try:
            email_data = (
                _get_gmail_message_summary(
                    service,
                    action[
                        "gmail_message_id"
                    ],
                )
            )

            draft_result = (
                generate_recruiter_reply_draft(
                    email_data=email_data,
                    action=action,
                )
            )

            # Send the approval card first.
            # If WhatsApp fails, the action remains
            # DRAFT_REQUESTED and can be retried.
            approval_preview = {
                **action,
                "draft":
                    draft_result.get(
                        "draft",
                        "",
                    ),
                "draft_confidence":
                    draft_result.get(
                        "confidence",
                        0.0,
                    ),
                "draft_needs_human_input":
                    draft_result.get(
                        "needs_human_input",
                        False,
                    ),
                "draft_missing_information":
                    draft_result.get(
                        "missing_information",
                        [],
                    ),
                "draft_reason":
                    draft_result.get(
                        "reason",
                        "",
                    ),
            }

            whatsapp_result = (
                send_draft_for_approval(
                    approval_preview
                )
            )

            updated = save_generated_draft(
                action_id,
                draft_result,
            )

            update_pending_action(
                action_id=action_id,
                status="AWAITING_APPROVAL",
                result={
                    "status":
                        "draft_generated_and_sent_for_approval",
                    "whatsapp_notification":
                        whatsapp_result,
                },
            )

            processed += 1

            results.append(
                {
                    "action_id":
                        action_id,
                    "status":
                        updated.get(
                            "status"
                        ),
                    "draft_confidence":
                        draft_result.get(
                            "confidence"
                        ),
                }
            )

        except Exception as error:
            failed += 1

            results.append(
                {
                    "action_id":
                        action_id,
                    "status":
                        "failed",
                    "error":
                        repr(error),
                }
            )

    return {
        "status": "completed",
        "found": len(actions),
        "processed": processed,
        "failed": failed,
        "results": results,
    }


def _process_send_approvals() -> dict:
    actions = _actions_with_status(
        "SEND_APPROVED"
    )

    results = []
    sent = 0
    failed = 0

    for action in actions:
        action_id = action.get(
            "id",
            "",
        )

        try:
            result = send_approved_draft(
                action_id,
                execute=True,
            )

            if result.get(
                "status"
            ) == "sent":
                sent += 1

            results.append(
                result
            )

        except Exception as error:
            failed += 1

            results.append(
                {
                    "action_id":
                        action_id,
                    "status":
                        "failed",
                    "error":
                        repr(error),
                }
            )

    return {
        "status": "completed",
        "found": len(actions),
        "sent": sent,
        "failed": failed,
        "results": results,
    }


def _run_recruiter_hitl_discovery() -> dict:
    local_timezone = timezone(
        timedelta(hours=4)
    )

    today = datetime.now(
        local_timezone
    ).date()

    start_date = (
        today
        - timedelta(
            days=max(
                HITL_LOOKBACK_DAYS - 1,
                0,
            )
        )
    ).isoformat()

    try:
        return send_one_recruiter_reply_to_hitl(
            start_date=start_date,
            min_confidence=0.75,
        )

    except Exception as error:
        return {
            "status": "failed",
            "error": repr(error),
        }


def main() -> int:
    if not DASHBOARD_RECIPIENT:
        print(
            "ERROR: "
            "JOB_SEARCH_AGENT_DASHBOARD_RECIPIENT "
            "is not configured",
            file=sys.stderr,
        )
        return 2

    signal.signal(
        signal.SIGALRM,
        _handle_timeout,
    )

    signal.alarm(
        MAX_RUN_SECONDS
    )

    try:
        print(
            "=== JOB SEARCH AGENT RUN ==="
        )

        print()
        print(
            "=== REJECTION AUTOMATION ==="
        )

        reply_result = (
            send_all_replyable_rejections(
                start_date=
                    JOB_SEARCH_START_DATE,
                max_batch=
                    MAX_REPLY_BATCH,
            )
        )

        print(
            {
                "status":
                    reply_result.get(
                        "status"
                    ),
                "discovered_replyable":
                    reply_result.get(
                        "discovered_replyable"
                    ),
                "attempted":
                    reply_result.get(
                        "attempted"
                    ),
                "sent":
                    reply_result.get(
                        "sent"
                    ),
                "skipped":
                    reply_result.get(
                        "skipped"
                    ),
            }
        )

        print()
        print(
            "=== HITL DISCOVERY ==="
        )

        hitl_result = (
            _run_recruiter_hitl_discovery()
        )

        print(
            hitl_result
        )

        print()
        print(
            "=== DRAFT REQUESTS ==="
        )

        draft_result = (
            _process_draft_requests()
        )

        print(
            {
                "status":
                    draft_result.get(
                        "status"
                    ),
                "found":
                    draft_result.get(
                        "found"
                    ),
                "processed":
                    draft_result.get(
                        "processed"
                    ),
                "failed":
                    draft_result.get(
                        "failed"
                    ),
            }
        )

        print()
        print(
            "=== SEND APPROVALS ==="
        )

        send_result = (
            _process_send_approvals()
        )

        print(
            {
                "status":
                    send_result.get(
                        "status"
                    ),
                "found":
                    send_result.get(
                        "found"
                    ),
                "sent":
                    send_result.get(
                        "sent"
                    ),
                "failed":
                    send_result.get(
                        "failed"
                    ),
            }
        )

        print()
        print(
            "=== DASHBOARD ==="
        )

        dashboard_result = (
            email_job_search_dashboard(
                recipient=
                    DASHBOARD_RECIPIENT,
                start_date=
                    JOB_SEARCH_START_DATE,
                automation_stats=
                    reply_result,
            )
        )

        print(
            dashboard_result
        )

        if dashboard_result.get(
            "status"
        ) != "sent":
            return 1

        return 0

    except JobSearchRunTimeout as error:
        print(
            f"ERROR: {error}",
            file=sys.stderr,
        )

        return 124

    except Exception as error:
        print(
            f"ERROR: {error!r}",
            file=sys.stderr,
        )

        return 1

    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(
        main()
    )