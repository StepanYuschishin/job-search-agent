"""Scheduled entry point for the Job Search Agent."""

from __future__ import annotations

import os
import signal
import sys

from job_search import (
    DEFAULT_START_DATE,
    MAX_REPLY_BATCH,
    email_job_search_dashboard,
    send_all_replyable_rejections,
)


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


class JobSearchRunTimeout(Exception):
    """Raised when a scheduled run exceeds its runtime ceiling."""


def _handle_timeout(signum, frame):
    raise JobSearchRunTimeout(
        f"Job Search Agent run exceeded {MAX_RUN_SECONDS} seconds"
    )


def main() -> int:
    if not DASHBOARD_RECIPIENT:
        print(
            "ERROR: JOB_SEARCH_AGENT_DASHBOARD_RECIPIENT is not configured",
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

        reply_result = send_all_replyable_rejections(
            start_date=JOB_SEARCH_START_DATE,
            max_batch=MAX_REPLY_BATCH,
        )

        print(
            "REJECTION REPLIES:"
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

        dashboard_result = email_job_search_dashboard(
            recipient=DASHBOARD_RECIPIENT,
            start_date=JOB_SEARCH_START_DATE,
            automation_stats=reply_result,
        )

        print(
            "DASHBOARD:"
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

    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(
        main()
    )