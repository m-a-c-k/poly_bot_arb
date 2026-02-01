# Kalshi-Polymarket Arbitrage Bot

Automated arbitrage trading bot that finds and executes price discrepancies between Kalshi and Polymarket prediction markets.

## Overview

The bot identifies arbitrage opportunities by:
- Monitoring sports markets (NFL, NBA, CBB) on both platforms
- Dynamically matching games using team abbreviations from event slugs/tickers
- Finding hedged positions where buying both sides guarantees profit
- Executing simultaneous market orders with Fill-or-Kill (FOK)

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Create `.env` file with credentials:
```bash
# Kalshi API
KALSHI_API_KEY_ID=your_key_id
KALSHI_PRIVATE_KEY_PEM=/path/to/private_key.pem

# Polymarket Wallet
PRIVATE_KEY=0x...  # Wallet private key for signing orders
```

### 3. Run Bot
```bash
# Start with nohup (recommended)
nohup python3 -u kalshi_poly_arb_live.py >> bot_log.txt 2>&1 &

# Or use the wrapper script
./run_bot.sh

# Monitor
tail -f bot_log.txt
```

## Configuration

Edit these constants in `kalshi_poly_arb_live.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_POSITION` | $8.00 | Maximum position size per trade (USD) |
| `MIN_PROFIT` | 0.5% | Minimum profit threshold to execute |
| `TEST_ORDER_SIZE` | 2.0 | Test mode order size (shares) |
| `LIQUIDITY_PERCENT` | 30% | Max % of balance to use per trade |
| `POLL_INTERVAL` | 15s | Time between market scans |
| `DRY_RUN` | False | Set True for simulation mode |
| `TEST_TINY_ORDER` | True | Use fixed test size (ignore position calc) |

## How It Works

### 1. Dynamic Game Matching
Instead of hardcoded team dictionaries, the bot:
- Extracts team abbreviations from Polymarket slugs (`nfl-buf-den-2026-01-17` → `buf-den`)
- Parses Kalshi tickers (`KXNFLGAME-26JAN17BUFDEN` → `buf-den`)
- Creates unified game keys (`nfl:buf-den`) for matching

### 2. Market Type Detection
Supports:
- **Moneyline/Winner**: Team to win the game
- **Spread**: Team to cover the spread (e.g., BUF -3.5)
- **Totals**: Over/Under point totals

### 3. Execution Strategy
**Critical: Polymarket placed FIRST, Kalshi SECOND**

Reasoning:
- Polymarket is slower and more error-prone
- Kalshi fills instantly (can't cancel in time)
- If Polymarket fails → clean exit, no Kalshi order placed
- If Kalshi fails after Polymarket → auto-close Polymarket position

**Flow:**
```
1. Place Polymarket order (market taker, FOK)
   ❌ Fails → Exit safely (no positions)
   ✅ Success → Continue to step 2

2. Place Kalshi order (market taker, FOK)
   ❌ Fails → EMERGENCY: Sell Polymarket position at market
   ✅ Success → Both legs filled, arbitrage locked
```

### 4. Safety Features

**Order Execution:**
- ✅ Market orders with FOK (Fill-or-Kill) on both platforms
- ✅ Never uses limit orders (always crosses spread)
- ✅ Fresh orderbook fetched before each order
- ✅ Liquidity checks (reject if insufficient)

**Position Safety:**
- ✅ Auto-close on failed leg (emergency flatten)
- ✅ Naked position detection (stops bot)
- ✅ Both-legs-or-none execution
- ✅ Position verification after fill

**Capital Management:**
- ✅ 30% liquidity limit per trade
- ✅ Balance checks on both platforms
- ✅ 40% loss threshold (kills bot)
- ✅ Minimum $1.00 order value (Polymarket requirement)

**Market Quality:**
- ✅ Integer contract requirement for Kalshi
- ✅ Market liquidity analysis (volume, open interest)
- ✅ 0.5% minimum profit threshold
- ✅ $1.00 max cost per share filter

## Files

### Core
- `kalshi_poly_arb_live.py` - Main trading bot
- `run_bot.sh` - Auto-restart wrapper script

### Utilities
- `bot_log.txt` - Execution log (auto-generated)
- `kalshi_poly_arb_trades.json` - Trade history log

## Monitoring

### Check Bot Status
```bash
ps aux | grep kalshi_poly_arb_live.py
```

### View Live Log
```bash
tail -f bot_log.txt
```

### Stop Bot
```bash
pkill -f kalshi_poly_arb_live.py
```

## Trade Log Format

Each trade is logged with:
```json
{
  "timestamp": 1768363196.22,
  "type": "Kalshi BUF YES + Poly DEN YES",
  "game": "nfl:buf-den",
  "market_type": "winner",
  "cost": 0.9674,
  "profit": 0.0326,
  "roi": 0.0337,
  "position_size": 2.0,
  "success": true,
  "both_legs_filled": true
}
```

## Emergency Procedures

### Naked Position Created
If bot logs `NAKED POSITION DETECTED`:

1. Check open positions:
```bash
# Kalshi: check your portfolio
# Polymarket: check your positions

2. Manually close the position on the filled platform
3. Update trade log: set `both_legs_filled: true`
4. Restart bot

### Bot Won't Start (Naked Position)
Bot refuses to start if it detects unclosed naked positions from previous run.

Fix: Manually close any open positions, then update the last trade entry in `kalshi_poly_arb_trades.json` to show `"both_legs_filled": true`.

## Development

### Test Scripts (gitignored)
- `test_*.py` - Various unit tests
- `debug_*.py` - Debugging utilities
- `check_*.py` - Position/order checkers

### Adding New Sports
Edit `get_kalshi_games()` and `get_polymarket_games()` to add series tickers:
```python
series_configs = [
    ('KXNFL', 'nfl'),
    ('KXNBA', 'nba'),
    ('KXNCAAMB', 'cbb'),
    # Add new series here
]
```

Update team search terms in `get_team_search_terms()` for proper outcome matching.

## Troubleshooting

### "OrderBookSummary object has no attribute 'get'"
Fixed: Code now uses `.asks` and `.bids` attributes correctly.

### "Order value < $1.00 minimum"
Polymarket requires minimum $1.00 order value. Increase `TEST_ORDER_SIZE` or position.

### "Insufficient liquidity"
Order size exceeds available orderbook depth. Bot will skip the trade.

### Both platforms use limit orders?
No - both use **market orders with FOK** (Fill-or-Kill). They cross the spread for immediate execution.

## API Rate Limits

- **Kalshi**: ~200 requests/minute
- **Polymarket**: No documented limit (uses websocket + REST)

Bot respects limits with 15s poll intervals.

## License

Private use only.
