#!/bin/bash
# RipForge Disc Detection Script
# Called by udev when a disc is inserted
# Notifies RipForge to start the rip process

DEVICE="${1:-/dev/sr0}"
RIPFORGE_URL="http://localhost:8081"
LOG="/home/paul/ripforge/logs/disc-detect.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG"
}

# Ensure log directory exists
mkdir -p "$(dirname "$LOG")"

log "Disc inserted: $DEVICE"

# Wait a moment for disc to spin up
sleep 3

# Check if disc is actually readable
if ! blkid "$DEVICE" >/dev/null 2>&1; then
    log "Disc not readable yet, waiting..."
    sleep 5
fi

# Notify RipForge API to start rip
response=$(curl -s -X POST "$RIPFORGE_URL/api/rip/start" \
    -H "Content-Type: application/json" \
    -d "{\"device\": \"$DEVICE\"}" 2>&1)

log "RipForge response: $response"

# If RipForge isn't running, log it
if [[ "$response" == *"Connection refused"* ]]; then
    log "WARNING: RipForge not running, disc will need manual rip"
fi
