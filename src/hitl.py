from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = BASE_DIR / "state"
STATE_DIR.mkdir(exist_ok=True)

PENDING_ACTIONS_FILE = STATE_DIR / "pending-actions.json"


OPEN_STATUSES = {
    "PENDING_HUMAN",
    "DRAFT_REQUESTED",
    "AWAITING_APPROVAL",
    "SEND_APPROVED",
    "EDIT_REQUESTED",
}


TERMINAL_STATUSES = {
    "COMPLETED",
    "IGNORED",
    "EXPIRED",
    "FAILED",
}


def _now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _load_pending_actions() -> dict:
    if not PENDING_ACTIONS_FILE.exists():
        return {}

    try:
        return json.loads(
            PENDING_ACTIONS_FILE.read_text()
        )
    except json.JSONDecodeError:
        return {}


def _save_pending_actions(
    actions: dict,
) -> None:
    PENDING_ACTIONS_FILE.write_text(
        json.dumps(
            actions,
            indent=2,
            ensure_ascii=False,
        )
    )


def find_open_action(
    gmail_message_id: str,
    gmail_thread_id: str = "",
) -> dict | None:
    actions = _load_pending_actions()

    for action in actions.values():
        if action.get("status") not in OPEN_STATUSES:
            continue

        if (
            action.get("gmail_message_id")
            == gmail_message_id
        ):
            return action

        if (
            gmail_thread_id
            and action.get("gmail_thread_id")
            == gmail_thread_id
        ):
            return action

    return None


def get_latest_action_by_status(
    statuses: set[str],
) -> dict | None:
    actions = _load_pending_actions()

    matching = [
        action
        for action in actions.values()
        if action.get("status") in statuses
    ]

    if not matching:
        return None

    matching.sort(
        key=lambda action: action.get(
            "updated_at",
            action.get(
                "created_at",
                "",
            ),
        ),
        reverse=True,
    )

    return matching[0]


def get_latest_pending_action() -> dict | None:
    return get_latest_action_by_status(
        {
            "PENDING_HUMAN",
        }
    )


def get_latest_awaiting_approval_action() -> dict | None:
    return get_latest_action_by_status(
        {
            "AWAITING_APPROVAL",
        }
    )


def create_pending_action(
    message: dict,
    recommended_action: str = "REVIEW",
    decision_confidence: float = 0.0,
    decision_reason: str = "",
) -> dict:
    gmail_message_id = str(
        message.get("id", "")
    ).strip()

    gmail_thread_id = str(
        message.get("thread_id", "")
    ).strip()

    if not gmail_message_id:
        raise ValueError(
            "gmail_message_id is required"
        )

    existing = find_open_action(
        gmail_message_id=gmail_message_id,
        gmail_thread_id=gmail_thread_id,
    )

    if existing:
        return existing

    action_id = uuid4().hex

    now = _now_iso()

    action = {
        "id": action_id,
        "created_at": now,
        "updated_at": now,
        "status": "PENDING_HUMAN",

        "gmail_message_id":
            gmail_message_id,
        "gmail_thread_id":
            gmail_thread_id,

        "subject":
            message.get(
                "subject",
                "",
            ),
        "from":
            message.get(
                "from",
                "",
            ),

        "classification":
            message.get(
                "label",
                "",
            ),
        "confidence":
            message.get(
                "confidence",
                0.0,
            ),
        "reason":
            message.get(
                "reason",
                "",
            ),

        "recommended_action":
            recommended_action,
        "decision_confidence":
            decision_confidence,
        "decision_reason":
            decision_reason,

        "human_decision": None,
        "human_decision_at": None,

        "approval_decision": None,
        "approval_decision_at": None,

        "result": None,
        "completed_at": None,
    }

    actions = _load_pending_actions()
    actions[action_id] = action

    _save_pending_actions(
        actions
    )

    return action


def update_pending_action(
    action_id: str,
    status: str,
    human_decision: str | None = None,
    result: dict | None = None,
) -> dict:
    actions = _load_pending_actions()

    action = actions.get(
        action_id
    )

    if not action:
        raise KeyError(
            f"Unknown pending action: {action_id}"
        )

    action["status"] = status
    action["updated_at"] = _now_iso()

    if human_decision is not None:
        action["human_decision"] = (
            human_decision
        )
        action["human_decision_at"] = (
            _now_iso()
        )

    if result is not None:
        action["result"] = result

    if status in TERMINAL_STATUSES:
        action["completed_at"] = (
            _now_iso()
        )

    actions[action_id] = action

    _save_pending_actions(
        actions
    )

    return action


def record_human_decision(
    action_id: str,
    decision: str,
) -> dict:
    decision = decision.strip().upper()

    if decision not in {
        "DRAFT",
        "IGNORE",
    }:
        raise ValueError(
            f"Unsupported human decision: {decision}"
        )

    actions = _load_pending_actions()

    action = actions.get(
        action_id
    )

    if not action:
        raise KeyError(
            f"Unknown action_id: {action_id}"
        )

    if action.get("status") != "PENDING_HUMAN":
        raise ValueError(
            f"Action is not pending human review: {action_id}"
        )

    now = _now_iso()

    action["human_decision"] = decision
    action["human_decision_at"] = now
    action["updated_at"] = now

    if decision == "IGNORE":
        action["status"] = "IGNORED"
        action["completed_at"] = now
        action["result"] = {
            "status": "ignored_by_human",
        }

    else:
        action["status"] = "DRAFT_REQUESTED"
        action["result"] = {
            "status": "draft_requested",
        }

    actions[action_id] = action

    _save_pending_actions(
        actions
    )

    return action


def save_generated_draft(
    action_id: str,
    draft_result: dict,
) -> dict:
    actions = _load_pending_actions()

    action = actions.get(
        action_id
    )

    if not action:
        raise KeyError(
            f"Unknown action_id: {action_id}"
        )

    if action.get("status") != "DRAFT_REQUESTED":
        raise ValueError(
            f"Action is not ready for draft generation: {action_id}"
        )

    action["draft"] = draft_result.get(
        "draft",
        "",
    )

    action["draft_confidence"] = draft_result.get(
        "confidence",
        0.0,
    )

    action["draft_needs_human_input"] = draft_result.get(
        "needs_human_input",
        False,
    )

    action["draft_missing_information"] = draft_result.get(
        "missing_information",
        [],
    )

    action["draft_reason"] = draft_result.get(
        "reason",
        "",
    )

    action["status"] = "AWAITING_APPROVAL"
    action["updated_at"] = _now_iso()

    action["result"] = {
        "status": "draft_generated",
    }

    actions[action_id] = action

    _save_pending_actions(
        actions
    )

    return action


def record_approval_decision(
    action_id: str,
    decision: str,
) -> dict:
    decision = decision.strip().upper()

    if decision not in {
        "SEND",
        "EDIT",
        "IGNORE",
    }:
        raise ValueError(
            f"Unsupported approval decision: {decision}"
        )

    actions = _load_pending_actions()

    action = actions.get(
        action_id
    )

    if not action:
        raise KeyError(
            f"Unknown action_id: {action_id}"
        )

    if action.get("status") != "AWAITING_APPROVAL":
        raise ValueError(
            f"Action is not awaiting approval: {action_id}"
        )

    now = _now_iso()

    action["approval_decision"] = decision
    action["approval_decision_at"] = now
    action["updated_at"] = now

    if decision == "SEND":
        action["status"] = "SEND_APPROVED"
        action["result"] = {
            "status": "send_approved",
        }

    elif decision == "EDIT":
        action["status"] = "EDIT_REQUESTED"
        action["result"] = {
            "status": "edit_requested",
        }

    else:
        action["status"] = "IGNORED"
        action["completed_at"] = now
        action["result"] = {
            "status": "ignored_by_human",
        }

    actions[action_id] = action

    _save_pending_actions(
        actions
    )

    return action

def get_action(
    action_id: str,
) -> dict | None:
    actions = _load_pending_actions()

    return actions.get(
        action_id
    )

