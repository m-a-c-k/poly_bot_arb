#!/bin/bash
# Check arbitrage bot status

cd "$(dirname "$0")"
TRADE_LOG="kalshi_poly_arb_trades.json"
LOG_FILE="bot_log.txt"

echo "========================================"
echo "Kalshi-Polymarket Arbitrage Bot Status"
echo "========================================"
echo

# Check if bot is running
echo "Bot Process:"
if ps aux | grep -v grep | grep kalshi_poly_arb_live.py > /dev/null; then
    PID=$(pgrep -f kalshi_poly_arb_live.py)
    echo "  ✓ Running (PID: $PID)"
    UPTIME=$(ps -o etime= -p $PID | tr -d ' ')
    echo "  Uptime: $UPTIME"
else
    echo "  ✗ Not running"
fi
echo

# Check last few log entries
echo "Recent Log Activity:"
if [ -f "$LOG_FILE" ]; then
    echo "  Last 5 lines of log:"
    tail -5 "$LOG_FILE" | sed 's/^/    /'
else
    echo "  No log file found"
fi
echo

# Check trade statistics
echo "Trade Statistics:"
if [ -f "$TRADE_LOG" ] && [ -s "$TRADE_LOG" ]; then
    TOTAL=$(python3 -c "import json; print(len(json.load(open('$TRADE_LOG'))))" 2>/dev/null || echo "0")
    SUCCESS=$(python3 -c "import json; t=json.load(open('$TRADE_LOG')); print(len([x for x in t if x.get('success')]))" 2>/dev/null || echo "0")

    # Calculate total profit
    PROFIT=$(python3 -c "
import json
t=json.load(open('$TRADE_LOG'))
profits=[x.get('locked_profit',0)*x.get('position_size',0) for x in t if x.get('both_legs_filled')]
print(f'\${sum(profits):.4f}')
" 2>/dev/null || echo "0")

    echo "  Total trades: $TOTAL"
    echo "  Successful: $SUCCESS"
    echo "  Total profit: \$$PROFIT"

    # Last trade
    echo
    echo "Last Trade:"
    python3 -c "
import json
with open('$TRADE_LOG') as f:
    t=json.load(f)
    if t:
        last=t[-1]
        import datetime
        dt=datetime.datetime.fromtimestamp(last.get('timestamp',0))
        print(f\"  Time: {dt.strftime('%Y-%m-%d %H:%M')}\")
        print(f\"  Type: {last.get('type', 'N/A')}\")
        print(f\"  ROI: {last.get('roi',0)*100:.2f}%\")
" 2>/dev/null
else
    echo "  No trades yet"
fi

echo
echo "========================================"
