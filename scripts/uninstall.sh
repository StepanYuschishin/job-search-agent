#!/usr/bin/env bash

set -euo pipefail

LABEL="com.job-search-agent"
PLIST="$HOME/Library/LaunchAgents/com.job-search-agent.plist"

echo "===================================="
echo " Job Search Agent Uninstaller"
echo "===================================="
echo ""

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "Stopping scheduler..."

    launchctl bootout \
        "gui/$(id -u)" \
        "$PLIST" \
        2>/dev/null || true
else
    echo "Scheduler is not currently loaded."
fi

if [ -f "$PLIST" ]; then
    echo "Removing LaunchAgent..."
    rm "$PLIST"
fi

echo ""
echo "Uninstall complete."
echo ""
echo "The repository, .env, Gmail credentials,"
echo "OAuth token, state, and logs were NOT deleted."
echo ""
echo "To run manually:"
echo "python src/run.py"