from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

try:
    from .hitl import (
        get_latest_action_by_status,
        get_latest_awaiting_approval_action,
        get_latest_pending_action,
        record_approval_decision,
        record_human_decision,
    )
except ImportError:
    from hitl import (
        get_latest_action_by_status,
        get_latest_awaiting_approval_action,
        get_latest_pending_action,
        record_approval_decision,
        record_human_decision,
    )

try:
    from .whatsapp import send_text_message
except ImportError:
    from whatsapp import send_text_message


load_dotenv()


WEBHOOK_HOST = os.getenv(
    "WEBHOOK_HOST",
    "127.0.0.1",
)

WEBHOOK_PORT = int(
    os.getenv(
        "WEBHOOK_PORT",
        "8080",
    )
)

WHATSAPP_VERIFY_TOKEN = os.getenv(
    "WHATSAPP_VERIFY_TOKEN",
    "",
)


def _extract_text_messages(
    payload: dict,
) -> list[str]:
    messages_found = []

    for entry in payload.get(
        "entry",
        [],
    ):
        for change in entry.get(
            "changes",
            [],
        ):
            value = change.get(
                "value",
                {},
            )

            for message in value.get(
                "messages",
                [],
            ):
                if message.get(
                    "type"
                ) != "text":
                    continue

                body = (
                    message
                    .get(
                        "text",
                        {},
                    )
                    .get(
                        "body",
                        "",
                    )
                    .strip()
                )

                if body:
                    messages_found.append(
                        body
                    )

    return messages_found


def _process_human_command(
    text: str,
) -> dict:
    command = text.strip().upper()

    if command == "HELP":
        return {
            "status": "help_requested",
            "command": command,
        }

    if command == "DRAFT":
        action = get_latest_pending_action()

        if not action:
            return {
                "status": "no_pending_action",
                "command": command,
            }

        updated = record_human_decision(
            action_id=action["id"],
            decision="DRAFT",
        )

        return {
            "status": "decision_recorded",
            "action_id": updated["id"],
            "decision": "DRAFT",
            "action_status": updated["status"],
        }

    if command in {
        "SEND",
        "EDIT",
    }:
        action = (
            get_latest_awaiting_approval_action()
        )

        if not action:
            return {
                "status": "no_awaiting_approval_action",
                "command": command,
            }

        updated = record_approval_decision(
            action_id=action["id"],
            decision=command,
        )

        return {
            "status": "approval_recorded",
            "action_id": updated["id"],
            "decision": command,
            "action_status": updated["status"],
        }

    if command == "IGNORE":
        action = get_latest_action_by_status(
            {
                "PENDING_HUMAN",
                "AWAITING_APPROVAL",
            }
        )

        if not action:
            return {
                "status": "no_ignorable_action",
                "command": command,
            }

        if action.get("status") == "PENDING_HUMAN":
            updated = record_human_decision(
                action_id=action["id"],
                decision="IGNORE",
            )

        else:
            updated = record_approval_decision(
                action_id=action["id"],
                decision="IGNORE",
            )

        return {
            "status": "decision_recorded",
            "action_id": updated["id"],
            "decision": "IGNORE",
            "action_status": updated["status"],
        }

    return {
        "status": "ignored_command",
        "command": command,
    }


class WhatsAppWebhookHandler(
    BaseHTTPRequestHandler
):
    def _send_text(
        self,
        status_code: int,
        body: str,
    ) -> None:
        payload = body.encode(
            "utf-8"
        )

        self.send_response(
            status_code
        )

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(
                len(payload)
            ),
        )

        self.end_headers()

        self.wfile.write(
            payload
        )

    def do_GET(self) -> None:
        parsed = urlparse(
            self.path
        )

        if parsed.path != "/webhook":
            self._send_text(
                404,
                "Not Found",
            )
            return

        params = parse_qs(
            parsed.query
        )

        mode = params.get(
            "hub.mode",
            [""],
        )[0]

        verify_token = params.get(
            "hub.verify_token",
            [""],
        )[0]

        challenge = params.get(
            "hub.challenge",
            [""],
        )[0]

        if (
            mode == "subscribe"
            and WHATSAPP_VERIFY_TOKEN
            and verify_token
            == WHATSAPP_VERIFY_TOKEN
        ):
            print(
                "Webhook verification successful"
            )

            self._send_text(
                200,
                challenge,
            )
            return

        print(
            "Webhook verification failed"
        )

        self._send_text(
            403,
            "Forbidden",
        )

    def do_POST(self) -> None:
        parsed = urlparse(
            self.path
        )

        if parsed.path != "/webhook":
            self._send_text(
                404,
                "Not Found",
            )
            return

        content_length = int(
            self.headers.get(
                "Content-Length",
                "0",
            )
        )

        raw_body = self.rfile.read(
            content_length
        )

        try:
            payload = json.loads(
                raw_body.decode(
                    "utf-8"
                )
            )

        except json.JSONDecodeError:
            self._send_text(
                400,
                "Invalid JSON",
            )
            return

        print(
            "=== WHATSAPP WEBHOOK EVENT ==="
        )

        print(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
            )
        )

        try:
            text_messages = (
                _extract_text_messages(
                    payload
                )
            )

            for text_message in text_messages:
                result = (
                    _process_human_command(
                        text_message
                    )
                )

                print(
                    "HITL COMMAND RESULT:"
                )

                print(
                    result
                )

                if result.get("status") in {
                    "help_requested",
                    "ignored_command",
                }:
                    send_text_message(
                        "JOB SEARCH AGENT\n\n"
                        "Available commands:\n"
                        "DRAFT - generate a reply draft\n"
                        "SEND - send an approved draft\n"
                        "EDIT - request changes\n"
                        "IGNORE - close the current action\n"
                        "HELP - show these commands"
                    )

        except Exception as error:
            print(
                "Webhook command processing error:",
                repr(error),
            )

        self._send_text(
            200,
            "EVENT_RECEIVED",
        )


def run_webhook_server() -> None:
    if not WHATSAPP_VERIFY_TOKEN:
        raise RuntimeError(
            "WHATSAPP_VERIFY_TOKEN is missing"
        )

    server = HTTPServer(
        (
            WEBHOOK_HOST,
            WEBHOOK_PORT,
        ),
        WhatsAppWebhookHandler,
    )

    print(
        f"WhatsApp webhook listening on "
        f"http://{WEBHOOK_HOST}:{WEBHOOK_PORT}/webhook"
    )

    server.serve_forever()


if __name__ == "__main__":
    run_webhook_server()