#!/bin/bash
# RipForge Disc Detection Script
# Called by udev when a disc is inserted
# Notifies RipForge to trigger a scan

DEVICE="${1:-/dev/sr0}"
RIPFORGE_URL="http://localhost:8081"
LOG="/home/paul/ripforge/logs/activity.log"

log() {
    local level="${2:-INFO}"
    echo "$(date '+%Y-%m-%d %H:%M:%S') | $level | $1" >> "$LOG"
}

# Ensure log directory exists
mkdir -p "$(dirname "$LOG")"

log "Disc inserted in $DEVICE"

# Wait a moment for disc to spin up
sleep 3

# Check if disc is actually readable
if ! blkid "$DEVICE" >/dev/null 2>&1; then
    log "Waiting for disc to become readable..." "INFO"
    sleep 5
fi

# Check if disc is now readable
if blkid "$DEVICE" >/dev/null 2>&1; then
    log "Disc ready in $DEVICE"
else
    log "Disc not readable in $DEVICE" "WARN"
fi
