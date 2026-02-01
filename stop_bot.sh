#!/bin/bash
# Stop the Kalshi-Polymarket arbitrage bot

echo "Stopping bot..."
pkill -f kalshi_poly_arb_live.py

sleep 2

if ps aux | grep -v grep | grep kalshi_poly_arb_live.py > /dev/null; then
    echo "Bot still running - force killing..."
    pkill -9 -f kalshi_poly_arb_live.py
    sleep 1
fi

if ps aux | grep -v grep | grep kalshi_poly_arb_live.py > /dev/null; then
    echo "❌ Bot still running!"
    ps aux | grep kalshi_poly_arb_live.py | grep -v grep
else
    echo "✓ Bot stopped"
fi
