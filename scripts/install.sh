#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
ENV_FILE="$PROJECT_DIR/.env"
ENV_EXAMPLE="$PROJECT_DIR/.env.example"
CREDENTIALS_FILE="$PROJECT_DIR/credentials.json"
PLIST_TARGET="$HOME/Library/LaunchAgents/com.job-search-agent.plist"

echo "===================================="
echo " Job Search Agent Installer"
echo "===================================="
echo ""

cd "$PROJECT_DIR"

echo "Project directory:"
echo "$PROJECT_DIR"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is not installed."
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"

        echo "Created .env from .env.example."
        echo ""
        echo "ACTION REQUIRED:"
        echo "Open:"
        echo "$ENV_FILE"
        echo ""
        echo "Fill in:"
        echo "- OPENAI_API_KEY"
        echo "- JOB_SEARCH_AGENT_SELF_EMAIL"
        echo "- JOB_SEARCH_AGENT_DASHBOARD_RECIPIENT"
        echo "- JOB_SEARCH_AGENT_START_DATE"
        echo ""
        echo "Then run this installer again."
        exit 0
    fi

    echo "ERROR: .env and .env.example are both missing."
    exit 1
fi

if grep -q "your_openai_api_key_here" "$ENV_FILE"; then
    echo "ERROR: OPENAI_API_KEY is not configured in .env."
    exit 1
fi

if grep -q "your_email@example.com" "$ENV_FILE"; then
    echo "ERROR: email settings are not configured in .env."
    exit 1
fi

if [ ! -f "$CREDENTIALS_FILE" ]; then
    echo "ERROR: credentials.json is missing."
    echo ""
    echo "Add your Google OAuth credentials here:"
    echo "$CREDENTIALS_FILE"
    echo ""
    echo "Then run this installer again."
    exit 1
fi

echo "Creating virtual environment..."

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

echo ""
echo "Installing dependencies..."

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

echo ""
echo "Compiling Python files..."

"$VENV_DIR/bin/python" -m py_compile \
    src/job_search.py \
    src/run.py

echo "Compile OK"

echo ""
echo "Running Gmail authentication check..."

"$VENV_DIR/bin/python" - <<'PY'
from src.job_search import _get_gmail_service

service = _get_gmail_service()
profile = service.users().getProfile(userId="me").execute()

print("Gmail auth OK")
print("Connected account:", profile.get("emailAddress"))
PY

echo ""
echo "Installing scheduler..."

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_TARGET" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">

<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.job-search-agent</string>

    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python</string>
        <string>$PROJECT_DIR/src/run.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key>
            <integer>9</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>

        <dict>
            <key>Hour</key>
            <integer>18</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
    </array>

    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/job-search-agent.log</string>

    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/job-search-agent-error.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST_TARGET"

launchctl bootout \
    "gui/$(id -u)" \
    "$PLIST_TARGET" \
    2>/dev/null || true

launchctl bootstrap \
    "gui/$(id -u)" \
    "$PLIST_TARGET"

echo ""
echo "Scheduler installed successfully."

echo ""
echo "IMPORTANT:"
echo "The agent has NOT been started automatically."
echo "Review your .env settings and start date before the first run."

echo ""
echo "===================================="
echo " Installation complete"
echo "===================================="
echo ""

echo "The Job Search Agent is configured to run at:"
echo "09:00"
echo "18:00"

echo ""
echo "Logs:"
echo "$PROJECT_DIR/job-search-agent.log"
echo "$PROJECT_DIR/job-search-agent-error.log"

echo ""
echo "First manual run:"
echo "$VENV_DIR/bin/python $PROJECT_DIR/src/run.py"

echo ""
echo "After the first manual run, verify the dashboard and any automated actions."