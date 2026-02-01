#!/bin/bash
# Auto-restart wrapper for Kalshi-Polymarket Arbitrage Bot
# Restarts the bot automatically if it crashes

cd "$(dirname "$0")"
LOG_FILE="bot_log.txt"
BOT_SCRIPT="kalshi_poly_arb_live.py"
RESTART_DELAY=5

log_msg() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log_msg "========================================"
log_msg "Starting arbitrage bot wrapper..."
log_msg "Log file: $LOG_FILE"
log_msg "Bot script: $BOT_SCRIPT"
log_msg "========================================"

while true; do
    log_msg "Starting bot..."

    # Run bot with unbuffered output for real-time logs
    python3 -u "$BOT_SCRIPT" >> "$LOG_FILE" 2>&1

    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        log_msg "Bot exited normally (code 0)"
        log_msg "Bot requested shutdown - not restarting"
        break
    else
        log_msg "Bot crashed with exit code $EXIT_CODE"
    fi

    log_msg "Restarting in ${RESTART_DELAY}s..."
    sleep $RESTART_DELAY
done
