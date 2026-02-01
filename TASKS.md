# Kalshi-Polymarket Arb Bot - Next Session Tasks

## Completed
- ✅ Cleaned up repo (deleted 50+ test/debug files)
- ✅ Added `src/team_mappings.py` with explicit team matching (NBA, NFL, CBB)
- ✅ Added `run_bot.sh` auto-restart wrapper
- ✅ Added `check_status.sh` health check script
- ✅ Pushed to GitHub

## Pending

### High Priority
1. **Test the bot**
   ```bash
   cd ~/poly_bots
   ./run_bot.sh
   # Check logs: tail -f bot_log.txt
   # Check status: ./check_status.sh
   ```

2. **Add Kalshi team matching to `kalshi_poly_arb_live.py`**
   - Import `from src.team_mappings import match_teams, is_same_team`
   - Replace existing team matching logic with explicit alias matching
   - Add logging for unknown teams

3. **Fix sorted() type errors** (LSP warnings)
   - Line 556, 591, 597, 603, 609: `sorted([...str | None])`
   - Filter None values before sorting

### Medium Priority
4. **Improve game matching**
   - Add more team aliases as needed from bot logs
   - Handle "La" vs "Los Angeles" teams consistently

5. **Position tracking**
   - Track open positions across bot restarts
   - Auto-close positions when games resolve

### Low Priority
6. **Expand team coverage**
   - Add more CBB teams
   - Add NHL teams if interested

## Quick Commands Reference
```bash
# Start bot (auto-restarts)
./run_bot.sh

# Check status
./check_status.sh

# Stop bot
./stop_bot.sh

# View real-time logs
tail -f bot_log.txt

# View last 20 trades
tail -20 kalshi_poly_arb_trades.json
```

## Bot Status Check
```bash
./check_status.sh
```
Shows:
- Bot running status + PID
- Uptime
- Recent log activity
- Total trades + success rate
- Total profit
- Last trade details

## Current State
- **Repo:** https://github.com/m-a-c-k/poly_bots
- **Bot:** `kalshi_poly_arb_live.py` (84KB)
- **Requires:** Kalshi API credentials in `.env`
- **Max position:** $8 per trade
- **Min profit:** 0.5%

## Kalshi API Reference (from docs.kalshi.com)

### Key Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/exchange/status` | GET | Check if exchange is active |
| `/markets` | GET | Get all markets with filters |
| `/markets/{ticker}` | GET | Get specific market |
| `/markets/{ticker}/orderbook` | GET | Get orderbook |
| `/events` | GET | Get events (games) |
| `/series` | GET | Get series (templates) |
| `/orders` | POST | Create order |
| `/orders/{id}` | DELETE | Cancel order |
| `/portfolio/balance` | GET | Get balance (cents) |
| `/portfolio/positions` | GET | Get positions |
| `/portfolio/fills` | GET | Get fills |

### Sports Filtering
- GET `/search/filters/sports` - Get available sports filters

### Exchange Status Response
```json
{
  "exchange_active": true,
  "trading_active": true,
  "exchange_estimated_resume_time": null
}
```

### Market Status Values
- `unopened` - Market not yet trading
- `open` - Market actively trading
- `closed` - Market closed
- `settled` - Market settled

### Rate Limits
- 20 orders per batch max
- 200,000 open orders per user max
- ~200 requests/minute

## Files Structure
```
poly_bots/
├── kalshi_poly_arb_live.py   # Main bot (84KB)
├── run_bot.sh                # Auto-restart wrapper
├── stop_bot.sh               # Shutdown script
├── check_status.sh           # Health check
├── .env.example              # Template for credentials
├── README.md
├── STRATEGY.md
├── TASKS.md                  # This file
├── kalshi_poly_arb_trades.json  # Trade history (generated)
├── bot_log.txt               # Bot logs (generated)
└── src/
    └── team_mappings.py      # Team name matching
```
