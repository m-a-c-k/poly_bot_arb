# Kalshi ↔ Polymarket Arbitrage Bot Strategy

## Core Principle: EQUAL LEGS, BALANCED EXECUTION

### The Strategy

**Step 1: Liquidity Check (Both Sides)**
- Before placing ANY order, check liquidity on BOTH Kalshi AND Polymarket
- Only proceed if BOTH sides have sufficient liquidity
- Use only **7% of available liquidity** per trade (safety margin)

**Step 2: Simultaneous Order Placement**
- Place Kalshi order at current market price (not aggressive, not limit)
- Place Polymarket market order (FOK - Fill or Kill)
- Both orders go in TOGETHER to minimize timing gap
- This ensures balanced position entry

**Step 3: Equal Cost Execution**
- Kalshi: Use current bid/ask prices from market data (not 98¢ aggressive)
- Polymarket: Use current market prices for instant fills
- Both execute at FAIR VALUE, not slipped prices

**Step 4: Automatic Rollback**
- If Polymarket succeeds but Kalshi fails → cancel Polymarket (via reverse order)
- If Kalshi succeeds but Polymarket fails → cancel Kalshi (via API)
- NO NAKED POSITIONS - both legs must execute or neither does

## Why This Works

1. **Equal Legs**: Both sides trade at current market prices → no imbalance
2. **Simultaneous**: Orders placed together → less time for market to move
3. **7% Liquidity**: Never take all available liquidity → ensures fills + leaves room for others
4. **Market Orders**: No pending limit orders → instant execution or immediate rollback

## What Failed Before

❌ Placing Kalshi limit orders at fixed prices while Polymarket market orders executed
- Kalshi orders sat unfilled while Polymarket executed
- Created naked Polymarket positions ($223+ unhedged)
- No simultaneous execution

## Implementation

```python
# DO NOT use aggressive pricing (98¢)
# DO NOT place orders sequentially (one after other)
# DO use current market prices from fetched data
# DO place both orders in rapid succession
# DO check liquidity on BOTH sides first
# DO respect 7% liquidity limit per position
```
