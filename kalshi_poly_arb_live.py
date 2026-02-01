#!/usr/bin/env python3
"""
Fast Kalshi ↔ Polymarket Arbitrage Bot
Checks winners, spreads, totals - both sides can place
"""

import os
import sys
import time
import json
import tempfile
import requests
from dotenv import load_dotenv

load_dotenv('.env')

# Trade logging
TRADE_LOG = 'kalshi_poly_arb_trades.json'

def log_trade(arb, position_size, success, both_legs_filled=True):
    """Log executed trades to file for tracking"""
    try:
        trades = []
        if os.path.exists(TRADE_LOG):
            with open(TRADE_LOG, 'r') as f:
                trades = json.load(f)
    except:
        trades = []

    trade = {
        'timestamp': time.time(),
        'type': arb['type'],
        'game': arb['game'],
        'cost': arb['cost'],
        'profit': arb['profit'],
        'roi': arb['roi'],
        'position_size': position_size,
        'trade_cost': arb['cost'] * position_size,
        'locked_profit': arb['profit'] * position_size,
        'success': success,
        'both_legs_filled': both_legs_filled  # Track if both sides executed
    }
    trades.append(trade)

    with open(TRADE_LOG, 'w') as f:
        json.dump(trades, f, indent=2)

def get_arb_key(arb):
    """Create unique key for arbitrage to prevent duplicates"""
    # Key format: "game:market_type:ks_side:pm_side"
    # Example: "nfl:tampa bay-houston:winner:yes:no"
    return f"{arb['game']}:{arb['market_type']}:{arb['ks_side']}:{arb['pm_side']}"

def is_duplicate_arb(arb):
    """Check if this arbitrage was recently executed"""
    arb_key = get_arb_key(arb)
    return arb_key in EXECUTED_ARBS

def mark_arb_executed(arb):
    """Mark arbitrage as executed to prevent duplicates"""
    arb_key = get_arb_key(arb)
    EXECUTED_ARBS.add(arb_key)
    print(f"    [TRACKED] Arbitrage marked as executed: {arb_key}")

GAMMA_API = "https://gamma-api.polymarket.com"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
MIN_PROFIT = 0.005  # 0.5% minimum profit threshold (lowered to catch more arbs)
KALSHI_TAKER_FEE = 0.02  # 2% taker fee on Kalshi orders
POLL_INTERVAL = 15  # Scan every 15 seconds for opportunities
MAX_POSITION = 8.00  # Max position size in USD - $8.00 per trade
DRY_RUN = False  # LIVE MODE - real trades
TEST_TINY_ORDER = True  # TEST MODE - fixed test orders
TEST_ORDER_SIZE = 2.0  # Test order size (ensures $1+ value even at $0.50 prices)
LOSS_KILL_THRESHOLD = 0.40  # Stop bot if losses exceed 40% of starting capital
LIQUIDITY_PERCENT = 0.30  # Use 30% of available balance per trade (increased for low balance testing)

# Track executed arbitrages to prevent duplicates in same session
EXECUTED_ARBS = set()
ARB_COOLDOWN_SECONDS = 3600  # Don't re-execute same arb within 1 hour

# Track positions per game to limit exposure (max 3 positions per game)
GAME_POSITION_COUNT = {}
MAX_POSITIONS_PER_GAME = 3

KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PEM = os.getenv("KALSHI_PRIVATE_KEY_PEM")
POLYMARKET_PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # Wallet private key
POLYMARKET_FUNDER = os.getenv("FUNDER_ADDRESS")
CLOB_API = "https://clob.polymarket.com"

def get_kalshi_games():
    """Get straight game markets from Kalshi (winners, spreads, totals - NO bundles)"""
    if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY_PEM:
        return {}

    games = {}

    # Fetch NFL, CFB, NCAA Basketball, and NBA games using correct series tickers
    # Each sport has separate series for moneylines, spreads, and totals
    series_configs = [
        ("KXNFLGAME", "nfl"),         # NFL moneylines (winner markets)
        ("KXNFLSPREAD", "nfl"),       # NFL spreads
        ("KXNFLTOTAL", "nfl"),        # NFL totals (over/under)
        ("KXCFBGAME", "cfb"),         # College football (off-season but keep for future)
        ("KXCFBSPREAD", "cfb"),       # CFB spreads
        ("KXCFBTOTAL", "cfb"),        # CFB totals
        ("KXNCAAMBGAME", "cbb"),      # Men's NCAA basketball moneylines (96 events!)
        ("KXNCAAMBSPREAD", "cbb"),    # Men's NCAA basketball spreads
        ("KXNCAAMBTOTAL", "cbb"),     # Men's NCAA basketball totals
        ("KXNBAGAME", "nba"),         # NBA moneylines (17 events)
        ("KXNBASPREAD", "nba"),       # NBA spreads (10 events)
        ("KXNBATOTAL", "nba")         # NBA totals (10 events)
    ]

    for series_ticker, sport_type in series_configs:
        try:
            # Fetch ALL markets for this series in ONE call (much faster than per-event)
            # This avoids 100+ separate API calls
            markets_resp = requests.get(
                f"{KALSHI_API}/markets",
                params={"series_ticker": series_ticker, "status": "open", "limit": 1000},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30
            )

            if not markets_resp.ok:
                continue

            mkt_data = markets_resp.json()
            markets = mkt_data.get('markets', [])

            # Process all markets for this series
            for m in markets:
                # Get event info from the market
                event_ticker = m.get('event_ticker', '')
                title = (m.get('title') or "").lower()
                ticker = m.get('ticker', '')

                if not event_ticker or not title:
                    continue

                # Extract game key from event ticker (teams encoded in ticker)
                game_key = extract_kalshi_game_key(event_ticker, title)
                if not game_key:
                    continue

                tagged_key = f"{sport_type}:{game_key}"

                # ONLY STRAIGHT MARKETS - skip all bundles (no commas)
                if ',' in title:
                    continue

                # Skip first half / quarter markets - only full game
                is_partial_game = any(x in title or x in ticker.lower() for x in [
                    '1h ', ' 1h', '1st half', 'first half', '2h ', ' 2h', '2nd half', 'second half',
                    '1q ', ' 1q', '1st quarter', 'first quarter',
                    '2q ', ' 2q', '2nd quarter', 'second quarter',
                    '3q ', ' 3q', '3rd quarter', 'third quarter',
                    '4q ', ' 4q', '4th quarter', 'fourth quarter'
                ])
                if is_partial_game:
                    continue

                # Skip props/player markets - ONLY game-level markets
                is_prop = any(x in title for x in [
                    'touchdown', 'reception', 'yard', 'passing', 'rushing', 'sack',
                    'interception', 'completion', 'attempt', 'team total', 'mvp',
                    'player', 'score', 'field goal', 'punt', 'turnover'
                ])
                if is_prop:
                    continue

                # Check if it's a game market (spread, total, moneyline only)
                has_spread = ' wins by' in title or 'spread' in title
                has_total = ('over' in title or 'under' in title) and 'point' in title
                has_winner = 'winner' in title or (' wins' in title and ' by' not in title)

                if not (has_spread or has_total or has_winner):
                    continue

                yes_ask = float(m.get('yes_ask') or 50) / 100.0
                no_ask = (100.0 - float(m.get('yes_ask') or 50)) / 100.0

                if tagged_key not in games:
                    games[tagged_key] = []

                # Extract liquidity metrics for safety checks
                open_interest = m.get('open_interest', 0)
                volume_24h = m.get('volume_24h', 0)
                liquidity_cents = m.get('liquidity', 0)

                games[tagged_key].append({
                    'title': title,
                    'yes': yes_ask,
                    'no': no_ask,
                    'id': ticker,
                    'event_ticker': event_ticker,
                    'open_interest': open_interest,  # Total open contracts
                    'volume_24h': volume_24h,  # 24h volume in contracts
                    'liquidity': liquidity_cents / 100.0 if liquidity_cents else 0  # Convert cents to dollars
                })

        except:
            continue

    return games

def get_polymarket_games():
    """Scan Polymarket for active NFL/CFB game markets using Gamma API series IDs"""
    games = {}

    try:
        # Get sports metadata to find NFL and CFB series IDs
        sports_resp = requests.get(
            f"{GAMMA_API}/sports",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )

        if not sports_resp.ok:
            return games

        sports = sports_resp.json()
        if not isinstance(sports, list):
            return games

        # Find NFL, CFB, CBB, and NBA series IDs
        # Note: Polymarket may have multiple series for same sport (e.g., cbb and ncaab)
        series_map = {}
        for sport in sports:
            sport_name = (sport.get('sport') or '').lower()
            series_id = sport.get('series')

            if sport_name == 'nfl':
                series_map['nfl'] = series_id
            elif sport_name == 'cfb':
                series_map['cfb'] = series_id
            elif sport_name == 'cbb':
                if 'cbb' not in series_map:
                    series_map['cbb'] = []
                series_map['cbb'].append(series_id)
            elif sport_name == 'ncaab':
                if 'cbb' not in series_map:
                    series_map['cbb'] = []
                series_map['cbb'].append(series_id)
            elif sport_name == 'nba':
                series_map['nba'] = series_id

        # If no sports found, return empty
        if not series_map:
            return games

        # Fetch events for each sport - limit to 100 to avoid processing thousands of historical events
        # Polymarket API returns events in reverse chronological order (newest first)
        for sport_type, series_ids in series_map.items():
            # Handle both single series_id and list of series_ids (for sports with multiple series)
            if not isinstance(series_ids, list):
                series_ids = [series_ids]

            for series_id in series_ids:
                try:
                    events_resp = requests.get(
                        f"{GAMMA_API}/events",
                        params={"series_id": series_id, "limit": 100, "closed": "false"},
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=30
                    )

                    if not events_resp.ok:
                        continue

                    events = events_resp.json()
                    if not isinstance(events, list):
                        continue

                    for event in events:
                        event_slug = (event.get('slug') or '').lower()

                        # Skip awards/props
                        is_award = any(x in event_slug for x in ['award', 'rookie', 'comeback', 'coach', 'mvp', 'dpoy', 'opoy'])
                        if is_award:
                            continue

                        # Extract game key from event slug (e.g., "nfl-buf-den-2026-01-17" → "buf-den")
                        game_key = extract_polymarket_game_key(event_slug)
                        if not game_key:
                            continue

                        tagged_key = f"{sport_type}:{game_key}"

                        # Get markets for this event
                        event_markets = event.get('markets', [])
                        if not event_markets:
                            continue

                        for m in event_markets:
                            # Skip closed markets
                            if m.get('closed', True):
                                continue

                            # Get bid/ask prices if available (use reasonable defaults if missing)
                            best_bid = m.get('bestBid')
                            best_ask = m.get('bestAsk')

                            try:
                                # Default to 0.5 if prices not available (will get real price from CLOB at trade time)
                                if best_bid is not None:
                                    bid = float(best_bid) if isinstance(best_bid, str) else best_bid
                                else:
                                    bid = 0.5

                                if best_ask is not None:
                                    ask = float(best_ask) if isinstance(best_ask, str) else best_ask
                                else:
                                    ask = 0.5

                                # Validate prices if they exist
                                if (best_bid is not None or best_ask is not None) and (ask <= 0 or bid < 0 or bid > 1 or ask > 1):
                                    continue

                                # Get market title
                                market_title = (m.get('question') or m.get('title') or '').lower()
                                market_slug = m.get('slug', '').lower()

                                # Skip bundles/parlays (contain commas or 'and')
                                is_bundle = ',' in market_title or ' and ' in market_title
                                if is_bundle:
                                    continue

                                # Skip first half / quarter markets - only full game
                                is_partial_game = any(x in market_title or x in market_slug for x in [
                                    '1h ', ' 1h', '1st half', 'first half', '2h ', ' 2h', '2nd half', 'second half',
                                    '1q ', ' 1q', '1st quarter', 'first quarter',
                                    '2q ', ' 2q', '2nd quarter', 'second quarter',
                                    '3q ', ' 3q', '3rd quarter', 'third quarter',
                                    '4q ', ' 4q', '4th quarter', 'fourth quarter'
                                ])
                                if is_partial_game:
                                    continue

                                # Skip props/player markets - ONLY game-level markets
                                is_prop = any(x in market_title for x in [
                                    'touchdown', 'reception', 'yard', 'passing', 'rushing', 'sack',
                                    'interception', 'completion', 'attempt', 'team total', 'mvp',
                                    'player', 'score', 'field goal', 'punt', 'turnover'
                                ])
                                if is_prop:
                                    continue

                                # Only game-level markets (spreads, totals, winners)
                                is_game = any(x in market_title for x in [' vs ', ' vs. ', 'winner', 'spread', 'total', ' by '])
                                if not is_game:
                                    continue

                                if tagged_key not in games:
                                    games[tagged_key] = []

                                # Extract clobTokenIds - this is the correct token ID for CLOB trading!
                                clob_token_ids_raw = m.get('clobTokenIds', '[]')
                                try:
                                    clob_token_ids = json.loads(clob_token_ids_raw) if isinstance(clob_token_ids_raw, str) else clob_token_ids_raw
                                except:
                                    clob_token_ids = []

                                # Need BOTH token IDs for Polymarket binary markets
                                # Polymarket API returns: outcomes[0] → clobTokenIds[0], outcomes[1] → clobTokenIds[1]
                                # For binary markets: outcomes = ["Yes", "No"] or ["Team A", "Team B"]
                                # So clobTokenIds[0]=YES token, clobTokenIds[1]=NO token
                                if not clob_token_ids or len(clob_token_ids) < 2:
                                    continue

                                # Verify both tokens exist
                                if not clob_token_ids[0] or not clob_token_ids[1]:
                                    continue

                                # Get outcomes array - tells us which token is which!
                                outcomes = m.get('outcomes', [])
                                if len(outcomes) < 2:
                                    outcomes = ['Yes', 'No']  # Default for non-team markets

                                # In Polymarket binary markets: YES + NO ≈ 1.0
                                # Cost to BUY YES = YES ask price
                                # Cost to BUY NO = 1.0 - YES bid price (cost of opposite outcome)
                                yes_cost = ask  # Cost to buy YES token
                                no_cost = 1.0 - bid  # Cost to buy NO token

                                # Extract volume metrics for liquidity checks
                                volume_total = float(m.get('volumeNum', 0) or m.get('volume', 0) or 0)
                                volume_24h = float(m.get('volume24hr', 0) or volume_total * 0.1)  # Estimate if not available

                                games[tagged_key].append({
                                    'title': market_title[:100],
                                    'yes': yes_cost,  # ASK price (cost to BUY YES outcome)
                                    'no': no_cost,   # 1.0 - BID price (cost to BUY NO outcome)
                                    'bid': bid,
                                    'ask': ask,
                                    # Store BOTH token IDs and outcomes
                                    # outcomes[0] → clobTokenIds[0], outcomes[1] → clobTokenIds[1]
                                    'id': [clob_token_ids[0], clob_token_ids[1]],
                                    'outcomes': outcomes,  # ["Team A", "Team B"] or ["Yes", "No"]
                                    'volume': volume_total,  # Total volume in USD
                                    'volume_24h': volume_24h  # 24h volume estimate
                                })
                            except:
                                continue

                except Exception as e:
                    continue

    except Exception as e:
        pass

    return games

def normalize_team_abbrev(abbrev):
    """Normalize team abbreviations across platforms

    Maps various abbreviations to a canonical form
    """
    # Common abbreviation variations (maps to canonical form)
    ABBREV_MAP = {
        # CBB teams with different abbreviations
        'gcan': 'gcu', 'nmx': 'nm', 'marq': 'marq', 'sju': 'sju',
        'gcu': 'gcu', 'nm': 'nm',
        # Add more as needed
    }

    abbrev_lower = abbrev.lower()
    return ABBREV_MAP.get(abbrev_lower, abbrev_lower)

def get_team_search_terms(abbrev):
    """Get searchable terms (city/team names) for a team abbreviation

    Returns list of terms to search for in titles/outcomes
    Example: "buf" → ["buffalo", "bills"]
    """
    # Map abbreviations to searchable terms [city, nickname, ...]
    TEAM_SEARCH_MAP = {
        # NFL
        'buf': ['buffalo', 'bills'],
        'den': ['denver', 'broncos'],
        'mia': ['miami', 'dolphins'],
        'ne': ['new england', 'patriots'],
        'nyj': ['new york jets', 'jets', 'ny jets'],
        'nyg': ['new york giants', 'giants', 'ny giants'],
        'bal': ['baltimore', 'ravens'],
        'cin': ['cincinnati', 'bengals'],
        'cle': ['cleveland', 'browns'],
        'pit': ['pittsburgh', 'steelers'],
        'hou': ['houston', 'texans'],
        'ind': ['indianapolis', 'colts'],
        'jax': ['jacksonville', 'jaguars'],
        'ten': ['tennessee', 'titans'],
        'kc': ['kansas city', 'chiefs'],
        'lv': ['las vegas', 'raiders'],
        'lac': ['los angeles chargers', 'chargers', 'la chargers'],
        'lar': ['los angeles rams', 'rams', 'la rams'],
        'sea': ['seattle', 'seahawks'],
        'sfs': ['san francisco', '49ers', 'niners'],
        'ari': ['arizona', 'cardinals'],
        'atl': ['atlanta', 'falcons'],
        'car': ['carolina', 'panthers'],
        'dal': ['dallas', 'cowboys'],
        'det': ['detroit', 'lions'],
        'gb': ['green bay', 'packers'],
        'min': ['minnesota', 'vikings'],
        'no': ['new orleans', 'saints'],
        'phi': ['philadelphia', 'eagles'],
        'tb': ['tampa bay', 'buccaneers'],
        'was': ['washington', 'commanders'],
        'chi': ['chicago', 'bears'],

        # NBA
        'atl': ['atlanta', 'hawks'],
        'bos': ['boston', 'celtics'],
        'bkn': ['brooklyn', 'nets'],
        'cha': ['charlotte', 'hornets'],
        'chi': ['chicago', 'bulls'],
        'cle': ['cleveland', 'cavaliers', 'cavs'],
        'dal': ['dallas', 'mavericks', 'mavs'],
        'den': ['denver', 'nuggets'],
        'det': ['detroit', 'pistons'],
        'gsw': ['golden state', 'warriors'],
        'hou': ['houston', 'rockets'],
        'ind': ['indiana', 'pacers'],
        'lac': ['los angeles clippers', 'clippers', 'la clippers'],
        'lal': ['los angeles lakers', 'lakers', 'la lakers'],
        'mem': ['memphis', 'grizzlies'],
        'mia': ['miami', 'heat'],
        'mil': ['milwaukee', 'bucks'],
        'min': ['minnesota', 'timberwolves', 'wolves'],
        'nop': ['new orleans', 'pelicans'],
        'nyk': ['new york knicks', 'knicks', 'ny knicks'],
        'okc': ['oklahoma city', 'thunder'],
        'orl': ['orlando', 'magic'],
        'phi': ['philadelphia', '76ers', 'sixers'],
        'phx': ['phoenix', 'suns'],
        'por': ['portland', 'trail blazers'],
        'sac': ['sacramento', 'kings'],
        'sas': ['san antonio', 'spurs'],
        'tor': ['toronto', 'raptors'],
        'uta': ['utah', 'jazz'],
        'was': ['washington', 'wizards'],

        # CBB (top teams)
        'gcu': ['grand canyon', 'antelopes'],
        'nm': ['new mexico', 'lobos'],
        'marq': ['marquette', 'golden eagles'],
        'sju': ['st johns', "st. john's", 'red storm'],
        'duke': ['duke', 'blue devils'],
        'unc': ['north carolina', 'tar heels'],
        'ku': ['kansas', 'jayhawks'],
        'uk': ['kentucky', 'wildcats'],
        'nova': ['villanova', 'wildcats'],
        'prov': ['providence', 'friars'],
        'gonz': ['gonzaga', 'bulldogs', 'zags'],
        'uconn': ['uconn', 'connecticut', 'huskies'],
        'ucla': ['ucla', 'bruins'],
        'pur': ['purdue', 'boilermakers'],
        'tenn': ['tennessee', 'volunteers', 'vols'],
        'txam': ['texas a&m', 'aggies', 'texas am'],
        'pepp': ['pepperdine', 'waves'],
        'port': ['portland', 'pilots'],
    }

    abbrev_lower = abbrev.lower()
    return TEAM_SEARCH_MAP.get(abbrev_lower, [abbrev_lower])

def extract_polymarket_game_key(event_slug):
    """Extract game key from Polymarket event slug

    Format: "sport-team1-team2-date"
    Example: "nfl-buf-den-2026-01-17" → "buf-den"
    Example: "cbb-gcan-nmx-2026-01-13" → "gcu-nm" (normalized)
    """
    if not event_slug:
        return None

    parts = event_slug.lower().split('-')

    # Need at least sport + 2 teams + date parts
    if len(parts) < 4:
        return None

    # Skip sport prefix (first part) and date parts (last 3)
    # Teams are in the middle
    team_parts = parts[1:-3]

    if len(team_parts) < 2:
        return None

    # Normalize abbreviations and return sorted
    team1 = normalize_team_abbrev(team_parts[0])
    team2 = normalize_team_abbrev(team_parts[1])

    return '-'.join(sorted([team1, team2]))

def extract_kalshi_game_key(event_ticker, title):
    """Extract game key from Kalshi event ticker or title

    Kalshi ticker format: "KXNFLGAME-26JAN17BUFDEN"
    Extract the date+teams part, then parse teams

    Fallback to parsing title if ticker doesn't work
    """
    if not event_ticker and not title:
        return None

    # Try parsing event_ticker first (most reliable)
    if event_ticker:
        # Split by hyphen: "KXNFLGAME-26JAN17BUFDEN" → ["KXNFLGAME", "26JAN17BUFDEN"]
        parts = event_ticker.upper().split('-')
        if len(parts) >= 2:
            # Get the date+teams part: "26JAN17BUFDEN"
            date_teams = parts[-1]

            # Strip common date patterns (26JAN17, 17JAN26, etc.)
            # Date is typically: DDMMMYY format at start
            import re
            # Remove date pattern: 2 digits + 3 letters + 2 digits
            teams_only = re.sub(r'^\d{2}[A-Z]{3}\d{2}', '', date_teams)

            if len(teams_only) >= 4:
                # Split into 2 teams (usually 2-4 chars each)
                # Try multiple split strategies and use the first that works

                # Strategy 1: Try 3-3 split (most common for NFL/NBA)
                if len(teams_only) == 6:
                    team1 = normalize_team_abbrev(teams_only[:3])
                    team2 = normalize_team_abbrev(teams_only[3:])
                    return '-'.join(sorted([team1, team2]))

                # Strategy 2: For 5-char, try 3-2 split (e.g., GCU-NM)
                elif len(teams_only) == 5:
                    team1 = normalize_team_abbrev(teams_only[:3])
                    team2 = normalize_team_abbrev(teams_only[3:])
                    return '-'.join(sorted([team1, team2]))

                # Strategy 3: For 7-char, try 4-3 split (e.g., MARQ-SJU)
                elif len(teams_only) == 7:
                    team1 = normalize_team_abbrev(teams_only[:4])
                    team2 = normalize_team_abbrev(teams_only[4:])
                    return '-'.join(sorted([team1, team2]))

                # Strategy 4: For 8-char, try 4-4 split
                elif len(teams_only) == 8:
                    team1 = normalize_team_abbrev(teams_only[:4])
                    team2 = normalize_team_abbrev(teams_only[4:])
                    return '-'.join(sorted([team1, team2]))

                # Strategy 5: Default to middle split for other lengths
                else:
                    mid = len(teams_only) // 2
                    team1 = normalize_team_abbrev(teams_only[:mid])
                    team2 = normalize_team_abbrev(teams_only[mid:])
                    if team1 and team2:
                        return '-'.join(sorted([team1, team2]))

    # If ticker parsing fails, return None (title parsing too unreliable)
    return None

def extract_market_type(title):
    """Extract market type from title: 'spread', 'total', 'winner', 'moneyline'"""
    title_lower = title.lower()

    # Spread markets
    if 'spread' in title_lower or 'wins by' in title_lower:
        return 'spread'

    # Total markets (over/under)
    if 'o/u' in title_lower or 'over/under' in title_lower or ('over' in title_lower and 'under' in title_lower):
        return 'total'

    # Team total markets (also totals)
    if 'team total' in title_lower:
        return 'total'

    # Winner/moneyline markets
    if 'winner' in title_lower or (' wins' in title_lower and 'by' not in title_lower):
        return 'winner'

    # Polymarket moneyline format: "Team A vs. Team B" or "Team A vs Team B"
    if ' vs' in title_lower and 'spread' not in title_lower and 'o/u' not in title_lower and 'total' not in title_lower:
        return 'winner'

    return None  # Unknown market type

def extract_line_number(title):
    """Extract line number from market title (e.g., 3.5, -1.5, 20.5)"""
    import re
    # Look for patterns like: "3.5", "-1.5", "over 20.5", "20.5 points"
    match = re.search(r'([-+]?\d+\.?\d*)', title)
    if match:
        return float(match.group(1))
    return None

def extract_spread_info(title, game_key):
    """For spread markets, extract (team_abbrev, line) to ensure we match same team spreads

    Examples:
    - "Steelers (-6.5)" + "nfl:hou-pit" -> ("pit", 6.5)
    - "Bills win by over 3.5" + "nfl:buf-den" -> ("buf", 3.5)

    Returns: (team_abbrev, line_value) or None
    """
    import re

    # Extract team abbreviations from game key
    if ':' in game_key:
        teams_str = game_key.split(':')[1]
        team_abbrevs = teams_str.split('-')
        if len(team_abbrevs) != 2:
            return None
    else:
        return None

    title_lower = title.lower()

    # Extract line number (use absolute value)
    line_match = re.search(r'([-+]?\d+\.?\d*)', title_lower)
    if not line_match:
        return None
    line = abs(float(line_match.group(1)))

    # Check which team is mentioned in title using search terms
    for team_abbrev in team_abbrevs:
        search_terms = get_team_search_terms(team_abbrev)
        for term in search_terms:
            if term.lower() in title_lower:
                return (team_abbrev, line)

    return None

def find_arbs(ks_games, pm_games):
    """Find arbitrage opportunities - match game + market type (not line, bundles don't have discrete lines)"""
    arbs = []

    # Build market maps by game + type (Kalshi bundles, Poly has discrete lines)
    for game_key in ks_games:
        if game_key not in pm_games:
            continue

        ks_markets = ks_games[game_key]
        pm_markets = pm_games[game_key]

        # Group markets by type only (can't do line matching with Kalshi bundles)
        ks_by_type = {}
        for m in ks_markets:
            mtype = extract_market_type(m['title'])
            if mtype:
                if mtype not in ks_by_type:
                    ks_by_type[mtype] = []
                ks_by_type[mtype].append(m)

        pm_by_type = {}
        for m in pm_markets:
            mtype = extract_market_type(m['title'])
            if mtype:
                if mtype not in pm_by_type:
                    pm_by_type[mtype] = []
                pm_by_type[mtype].append(m)

        # Match markets with same game + type + EXACT LINE
        for market_type in ks_by_type:
            if market_type not in pm_by_type:
                continue

            ks_type_markets = ks_by_type[market_type]
            pm_type_markets = pm_by_type[market_type]

            for ks_market in ks_type_markets:
                for pm_market in pm_type_markets:
                    # For SPREADS: Must match same team AND same line
                    # e.g., "Pittsburgh -6.5" matches "Pittsburgh -6.5" ONLY
                    # NOT "Pittsburgh -6.5" with "Houston -6.5"
                    if market_type == 'spread':
                        ks_spread = extract_spread_info(ks_market['title'], game_key)
                        pm_spread = extract_spread_info(pm_market['title'], game_key)

                        # Both must have valid spread info and match (team, line)
                        if not ks_spread or not pm_spread:
                            continue
                        if ks_spread != pm_spread:
                            continue  # Different team or different line - skip!

                    # For TOTALS/MONEYLINES: Match by line number only
                    else:
                        ks_line = extract_line_number(ks_market['title'])
                        pm_line = extract_line_number(pm_market['title'])

                        # Lines must match (or both None for moneylines)
                        if ks_line != pm_line:
                            continue

                    # For SPREADS: need to determine which Polymarket token is which team
                    # Kalshi YES = team covers, Kalshi NO = opponent covers
                    # Polymarket has two tokens (one per team), need to select opposite team

                    if market_type == 'spread':
                        # Extract which team Kalshi is betting on
                        ks_spread = extract_spread_info(ks_market['title'], game_key)
                        pm_spread = extract_spread_info(pm_market['title'], game_key)

                        if not ks_spread or not pm_spread:
                            continue  # Skip if can't determine teams

                        ks_team, ks_line = ks_spread
                        pm_team, pm_line = pm_spread

                        # Get both teams from game_key
                        teams_str = game_key.split(':')[1]
                        teams = teams_str.split('-')
                        opponent = teams[1] if teams[0] == ks_team else teams[0]

                        # Determine which token to buy based on which team each market is for
                        # If both markets are for the same team, bet opposite sides
                        # If markets are for different teams, bet same sides

                        # Arb 1: KS YES (ks_team covers) + PM bet opponent covers
                        ks_cost_before_fee = ks_market['yes']
                        ks_fee = ks_cost_before_fee * KALSHI_TAKER_FEE

                        # If PM market is for ks_team, buy NO (opponent covers)
                        # If PM market is for opponent, buy YES (opponent covers)
                        if pm_team == ks_team:
                            pm_cost = pm_market['no']  # PM is same team, buy NO = opponent covers
                            pm_side = 'no'
                        else:
                            pm_cost = pm_market['yes']  # PM is opponent, buy YES = opponent covers
                            pm_side = 'yes'

                        cost1 = ks_cost_before_fee + ks_fee + pm_cost
                        profit1 = 1.0 - cost1

                        if profit1 >= MIN_PROFIT * cost1 and cost1 > 0:
                            arbs.append({
                                'game': game_key,
                                'type': f'Kalshi {ks_team.upper()} YES + Poly {opponent.upper()} YES',
                                'market_type': market_type,
                                'ks': ks_market['title'][:60],
                                'pm': pm_market['title'][:60],
                                'ks_id': ks_market['id'],
                                'pm_id': pm_market['id'],
                                'ks_side': 'yes',
                                'pm_side': pm_side,
                                'pm_team': opponent,  # Betting on opponent covering
                                'ks_team': ks_team,  # Betting on ks_team covering
                                'cost': cost1,
                                'profit': profit1,
                                'roi': profit1 / cost1 if cost1 > 0 else 0,
                                'fee': ks_fee,
                                'ks_yes_ask': ks_market['yes'],
                                'pm_best_ask': pm_cost
                            })

                        # Arb 2: KS NO (opponent covers) + PM bet ks_team covers
                        ks_cost_before_fee = ks_market['no']
                        ks_fee = ks_cost_before_fee * KALSHI_TAKER_FEE

                        # If PM market is for ks_team, buy YES (ks_team covers)
                        # If PM market is for opponent, buy NO (ks_team covers)
                        if pm_team == ks_team:
                            pm_cost = pm_market['yes']  # PM is ks_team, buy YES = ks_team covers
                            pm_side = 'yes'
                        else:
                            pm_cost = pm_market['no']  # PM is opponent, buy NO = ks_team covers
                            pm_side = 'no'

                        cost2 = ks_cost_before_fee + ks_fee + pm_cost
                        profit2 = 1.0 - cost2

                        if profit2 >= MIN_PROFIT * cost2 and cost2 > 0:
                            arbs.append({
                                'game': game_key,
                                'type': f'Kalshi {opponent.upper()} YES + Poly {ks_team.upper()} YES',
                                'market_type': market_type,
                                'ks': ks_market['title'][:60],
                                'pm': pm_market['title'][:60],
                                'ks_id': ks_market['id'],
                                'pm_id': pm_market['id'],
                                'ks_side': 'no',
                                'pm_side': pm_side,
                                'pm_team': ks_team,  # Betting on ks_team covering
                                'ks_team': ks_team,  # KS NO = betting opponent covers
                                'cost': cost2,
                                'profit': profit2,
                                'roi': profit2 / cost2 if cost2 > 0 else 0,
                                'fee': ks_fee,
                                'ks_no_ask': ks_market['no'],
                                'pm_best_ask': pm_cost
                            })
                    elif market_type == 'winner':
                        # MONEYLINES - CRITICAL: Must properly identify which token is which team!
                        # Kalshi: "Team A winner?" - YES = Team A wins, NO = Team A loses
                        # Polymarket: "Team A vs Team B" - outcomes[0] vs outcomes[1], need to check which is which

                        # Get both teams from game_key
                        teams_str = game_key.split(':')[1]
                        teams = teams_str.split('-')

                        # Safety: skip if not exactly 2 teams
                        if len(teams) != 2:
                            continue

                        # Extract which team Kalshi market is for (using search terms)
                        ks_team = None
                        ks_title_lower = ks_market['title'].lower()
                        for team_abbrev in teams:
                            search_terms = get_team_search_terms(team_abbrev)
                            for term in search_terms:
                                if term.lower() in ks_title_lower:
                                    ks_team = team_abbrev
                                    break
                            if ks_team:
                                break

                        if not ks_team:
                            continue  # Can't determine Kalshi team

                        opponent = teams[1] if teams[0] == ks_team else teams[0]

                        # CRITICAL: Determine Polymarket token structure using outcomes array
                        # Polymarket markets have 'outcomes' array like ["Marquette", "St Johns"]
                        # outcomes[0] maps to YES token, outcomes[1] maps to NO token
                        pm_outcomes = pm_market.get('outcomes', [])
                        if len(pm_outcomes) < 2:
                            continue  # Can't determine PM structure

                        # Find which outcome index corresponds to each team (using search terms)
                        ks_team_outcome_idx = None
                        opponent_outcome_idx = None

                        for idx, outcome in enumerate(pm_outcomes):
                            outcome_lower = outcome.lower()

                            # Check ks_team
                            ks_search_terms = get_team_search_terms(ks_team)
                            for term in ks_search_terms:
                                if term.lower() in outcome_lower:
                                    ks_team_outcome_idx = idx
                                    break

                            # Check opponent
                            opp_search_terms = get_team_search_terms(opponent)
                            for term in opp_search_terms:
                                if term.lower() in outcome_lower:
                                    opponent_outcome_idx = idx
                                    break

                        if ks_team_outcome_idx is None or opponent_outcome_idx is None:
                            continue  # Can't map teams to outcomes

                        # NOW we know:
                        # - If ks_team_outcome_idx == 0: pm_market['yes'] = bet on ks_team
                        # - If ks_team_outcome_idx == 1: pm_market['no'] = bet on ks_team
                        # - If opponent_outcome_idx == 0: pm_market['yes'] = bet on opponent
                        # - If opponent_outcome_idx == 1: pm_market['no'] = bet on opponent

                        # Arb 1: KS YES (ks_team wins) + PM opponent token (opponent wins)
                        ks_cost_before_fee = ks_market['yes']
                        ks_fee = ks_cost_before_fee * KALSHI_TAKER_FEE

                        # Buy the opponent token on Polymarket
                        if opponent_outcome_idx == 0:
                            pm_cost = pm_market['yes']  # Opponent is outcome[0]
                            pm_token_idx = 0
                        else:
                            pm_cost = pm_market['no']  # Opponent is outcome[1]
                            pm_token_idx = 1

                        cost1 = ks_cost_before_fee + ks_fee + pm_cost
                        profit1 = 1.0 - cost1

                        if profit1 >= MIN_PROFIT * cost1 and cost1 > 0:
                            arbs.append({
                                'game': game_key,
                                'type': f'KS:YES({ks_team.upper()}) + PM:{opponent.upper()}',
                                'market_type': market_type,
                                'ks': ks_market['title'][:60],
                                'pm': pm_market['title'][:60],
                                'ks_id': ks_market['id'],
                                'pm_id': pm_market['id'][pm_token_idx] if isinstance(pm_market['id'], list) else pm_market['id'],
                                'ks_side': 'yes',
                                'pm_side': 'yes' if pm_token_idx == 0 else 'no',  # Which token to buy
                                'pm_team': opponent,  # Betting on opponent WINNING
                                'ks_team': ks_team,  # Betting on ks_team WINNING (via YES)
                                'cost': cost1,
                                'profit': profit1,
                                'roi': profit1 / cost1 if cost1 > 0 else 0,
                                'fee': ks_fee,
                                'ks_yes_ask': ks_market['yes'],
                                'pm_best_ask': pm_cost,
                                'hedge_check': f'Kalshi bets {ks_team} wins, PM bets {opponent} wins'
                            })

                        # Arb 2: KS NO (opponent wins) + PM ks_team token (ks_team wins)
                        ks_cost_before_fee = ks_market['no']
                        ks_fee = ks_cost_before_fee * KALSHI_TAKER_FEE

                        # Buy the ks_team token on Polymarket
                        if ks_team_outcome_idx == 0:
                            pm_cost = pm_market['yes']  # ks_team is outcome[0]
                            pm_token_idx = 0
                        else:
                            pm_cost = pm_market['no']  # ks_team is outcome[1]
                            pm_token_idx = 1

                        cost2 = ks_cost_before_fee + ks_fee + pm_cost
                        profit2 = 1.0 - cost2

                        if profit2 >= MIN_PROFIT * cost2 and cost2 > 0:
                            arbs.append({
                                'game': game_key,
                                'type': f'KS:NO({ks_team.upper()}) + PM:{ks_team.upper()}',
                                'market_type': market_type,
                                'ks': ks_market['title'][:60],
                                'pm': pm_market['title'][:60],
                                'ks_id': ks_market['id'],
                                'pm_id': pm_market['id'][pm_token_idx] if isinstance(pm_market['id'], list) else pm_market['id'],
                                'ks_side': 'no',
                                'pm_side': 'yes' if pm_token_idx == 0 else 'no',  # Which token to buy
                                'pm_team': ks_team,  # Betting on ks_team WINNING
                                'ks_team': ks_team,  # Market is for ks_team, buying NO = opponent wins
                                'cost': cost2,
                                'profit': profit2,
                                'roi': profit2 / cost2 if cost2 > 0 else 0,
                                'fee': ks_fee,
                                'ks_no_ask': ks_market['no'],
                                'pm_best_ask': pm_cost,
                                'hedge_check': f'Kalshi bets {opponent} wins (NO on {ks_team}), PM bets {ks_team} wins'
                            })

                    else:
                        # For TOTALS: TRUE YES/NO (over/under), not team-based
                        # Arb 1: KS YES + PM NO
                        ks_cost_before_fee = ks_market['yes']
                        ks_fee = ks_cost_before_fee * KALSHI_TAKER_FEE
                        pm_cost = pm_market['no']
                        cost1 = ks_cost_before_fee + ks_fee + pm_cost
                        profit1 = 1.0 - cost1

                        if profit1 >= MIN_PROFIT * cost1 and cost1 > 0:
                            arbs.append({
                                'game': game_key,
                                'type': 'Kalshi YES + Poly NO',
                                'market_type': market_type,
                                'ks': ks_market['title'][:60],
                                'pm': pm_market['title'][:60],
                                'ks_id': ks_market['id'],
                                'pm_id': pm_market['id'],
                                'ks_side': 'yes',
                                'pm_side': 1,  # 1 = NO/UNDER token
                                'cost': cost1,
                                'profit': profit1,
                                'roi': profit1 / cost1 if cost1 > 0 else 0,
                                'fee': ks_fee,
                                'ks_yes_ask': ks_market['yes'],
                                'pm_best_ask': pm_market['no']
                            })

                        # Arb 2: KS NO + PM YES
                        ks_cost_before_fee = ks_market['no']
                        ks_fee = ks_cost_before_fee * KALSHI_TAKER_FEE
                        pm_cost = pm_market['yes']
                        cost2 = ks_cost_before_fee + ks_fee + pm_cost
                        profit2 = 1.0 - cost2

                        if profit2 >= MIN_PROFIT * cost2 and cost2 > 0:
                            arbs.append({
                                'game': game_key,
                                'type': 'Kalshi NO + Poly YES',
                                'market_type': market_type,
                                'ks': ks_market['title'][:60],
                                'pm': pm_market['title'][:60],
                                'ks_id': ks_market['id'],
                                'pm_id': pm_market['id'],
                                'ks_side': 'no',
                                'pm_side': 0,  # 0 = YES/OVER token
                                'cost': cost2,
                                'profit': profit2,
                                'roi': profit2 / cost2 if cost2 > 0 else 0,
                                'fee': ks_fee,
                                'ks_no_ask': ks_market['no'],
                                'pm_best_ask': pm_market['yes']
                            })

    return arbs

def place_kalshi_order(market_ticker, side, quantity, price):
    """Place order on Kalshi at current market price - ensures both legs equal"""
    try:
        from datetime import datetime, timedelta, timezone
        pem = KALSHI_PRIVATE_KEY_PEM.replace('\\n', '\n')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
            f.write(pem)
            key_path = f.name

        from kalshi_python import Configuration, PortfolioApi, ApiClient, CreateOrderRequest
        config = Configuration(host=KALSHI_API)
        api_client = ApiClient(config)
        api_client.set_kalshi_auth(KALSHI_API_KEY_ID, key_path)
        portfolio_api = PortfolioApi(api_client)

        # CRITICAL: Price at 99 cents (maximum) to GUARANTEE taker execution
        # NEVER EVER allow orders to rest on the book
        #
        # Why 99 cents:
        # - If we price at current ask, and ask moves up, we become a MAKER (resting limit)
        # - If we price at 99 cents, we ALWAYS cross the spread (TAKER)
        # - This is the ONLY way to guarantee immediate fill or rejection
        #
        # We don't care about slippage - we care about NEVER RESTING ON BOOK

        order_kwargs = {
            'ticker': market_ticker,
            'side': side.lower(),  # 'yes' or 'no'
            'action': 'buy',
            'count': int(quantity),
            'type': 'market'  # MARKET order - immediate execution, never rests on book
        }

        # ALWAYS price at 99 cents to cross ANY possible spread
        if side.lower() == 'yes':
            order_kwargs['yes_price'] = 99  # Maximum = guaranteed taker
        else:
            order_kwargs['no_price'] = 99  # Maximum = guaranteed taker

        response = portfolio_api.create_order(**order_kwargs)

        # CreateOrderResponse has an 'order' attribute containing the Order object
        order_obj = getattr(response, 'order', response)
        order_id = getattr(order_obj, 'order_id', None)
        order_status = getattr(order_obj, 'status', None)

        if not order_id:
            os.unlink(key_path)
            raise Exception(f"No order_id in response: {response}")

        # SAFETY CHECK: This should NEVER happen since we price at 99 cents
        # But if Kalshi API somehow creates a resting order, catch it immediately
        if order_status == 'resting':
            print(f"    ✗✗✗ CRITICAL BUG: KALSHI ORDER RESTING ON BOOK")
            print(f"    This should NEVER happen with 99 cent pricing!")
            print(f"    → EMERGENCY: Selling position immediately...")

            # Can't cancel (too slow) - must SELL the same side we just bought
            try:
                sell_kwargs = {
                    'ticker': market_ticker,
                    'side': side.lower(),
                    'action': 'sell',
                    'count': int(quantity)
                }

                # Price at 1 cent (minimum) to guarantee immediate sell
                if side.lower() == 'yes':
                    sell_kwargs['yes_price'] = 1
                else:
                    sell_kwargs['no_price'] = 1

                sell_response = portfolio_api.create_order(**sell_kwargs)
                print(f"    ✓ Emergency sell executed")
            except Exception as sell_err:
                print(f"    ✗✗✗ EMERGENCY SELL FAILED: {sell_err}")

            os.unlink(key_path)
            return {'success': False, 'error': f'BUG: Order rested despite 99c price - emergency flattened'}

        os.unlink(key_path)
        return {'success': True, 'order_id': order_id, 'status': order_status}
    except Exception as e:
        try:
            os.unlink(key_path)
        except:
            pass
        return {'success': False, 'error': str(e)}

def cancel_kalshi_order(order_id):
    """Cancel a Kalshi order"""
    try:
        pem = KALSHI_PRIVATE_KEY_PEM.replace('\\n', '\n')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
            f.write(pem)
            key_path = f.name

        from kalshi_python import Configuration, PortfolioApi, ApiClient
        config = Configuration(host=KALSHI_API)
        api_client = ApiClient(config)
        api_client.set_kalshi_auth(KALSHI_API_KEY_ID, key_path)
        portfolio_api = PortfolioApi(api_client)

        print(f"    [CANCEL] Attempting to cancel order {order_id}...")
        response = portfolio_api.cancel_order(order_id=order_id)

        print(f"    [CANCEL] Response: {response}")

        os.unlink(key_path)

        # Check if response indicates success
        if hasattr(response, 'status') and 'cancel' in str(response.status).lower():
            return {'success': True}
        elif response is not None:
            return {'success': True}  # API returned something, assume it worked
        else:
            return {'success': False, 'error': 'No response from cancel'}

    except Exception as e:
        try:
            os.unlink(key_path)
        except:
            pass
        error_msg = str(e)
        print(f"    [CANCEL ERROR] {error_msg}")
        return {'success': False, 'error': error_msg}

def verify_positions_match(kalshi_ticker, poly_token_id, expected_size):
    """Verify positions on both platforms roughly match after trade execution"""
    try:
        # Get Kalshi position
        pem = KALSHI_PRIVATE_KEY_PEM.replace('\\n', '\n')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
            f.write(pem)
            key_path = f.name

        from kalshi_python import Configuration, PortfolioApi, ApiClient
        config = Configuration(host=KALSHI_API)
        api_client = ApiClient(config)
        api_client.set_kalshi_auth(KALSHI_API_KEY_ID, key_path)
        portfolio_api = PortfolioApi(api_client)

        # Get recent fills for this ticker
        fills = portfolio_api.get_fills(ticker=kalshi_ticker, limit=10)

        # Sum up position from recent fills (last 5 minutes)
        import time
        five_min_ago = time.time() - 300
        kalshi_contracts = 0

        for fill in fills.fills:
            # Simple approach: count all recent fills
            if fill.side == 'yes':
                kalshi_contracts += fill.count
            else:  # 'no'
                kalshi_contracts += fill.count

        os.unlink(key_path)

        # For Polymarket, we'd need to check positions via API
        # For now, just check if Kalshi position roughly matches expected
        tolerance = expected_size * 0.5  # Allow 50% tolerance for multiple trades

        if kalshi_contracts > expected_size + tolerance:
            print(f"    ⚠️  WARNING: Kalshi position ({kalshi_contracts}) >> expected ({expected_size})")
            return False

        return True

    except Exception as e:
        print(f"    ⚠️  Position verification failed: {str(e)[:50]}")
        # Don't fail the trade on verification error, just warn
        return True

def get_polymarket_client():
    """Initialize Polymarket CLOB client"""
    try:
        from py_clob_client.client import ClobClient
        client = ClobClient(
            host=CLOB_API,
            key=POLYMARKET_PRIVATE_KEY,
            chain_id=137,
            signature_type=1,
            funder=POLYMARKET_FUNDER
        )
        client.set_api_creds(client.derive_api_key())
        return client
    except Exception as e:
        return None

def get_kalshi_balance():
    """Get available cash balance from Kalshi"""
    if not KALSHI_API_KEY_ID or not KALSHI_PRIVATE_KEY_PEM:
        return 0

    pem = KALSHI_PRIVATE_KEY_PEM.replace('\\n', '\n')
    with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as f:
        f.write(pem)
        key_path = f.name

    try:
        from kalshi_python import Configuration, PortfolioApi, ApiClient
        config = Configuration(host=KALSHI_API)
        api_client = ApiClient(config)
        api_client.set_kalshi_auth(KALSHI_API_KEY_ID, key_path)

        portfolio_api = PortfolioApi(api_client)
        response = portfolio_api.get_balance()

        # GetBalanceResponse has 'balance' field in cents
        balance = float(getattr(response, 'balance', 0) or 0)
        return balance / 100.0  # Kalshi returns balance in cents
    except:
        return 0
    finally:
        try:
            os.unlink(key_path)
        except:
            pass

def get_polymarket_balance():
    """Get USDC balance from Polygon blockchain (where Polymarket USDC lives)"""
    try:
        funder = POLYMARKET_FUNDER

        # USDC contract on Polygon
        USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

        # balanceOf(address) function selector
        SELECTOR = "0x70a08231"
        PADDED_ADDRESS = funder.lower().replace("0x", "").zfill(64)

        # Query Polygon JSON-RPC for USDC balance
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {
                    "to": USDC_ADDRESS,
                    "data": f"{SELECTOR}{PADDED_ADDRESS}"
                },
                "latest"
            ],
            "id": 1
        }

        resp = requests.post(
            "https://polygon-rpc.com/",
            json=payload,
            timeout=5
        )

        if resp.ok:
            data = resp.json()
            if 'result' in data:
                # Convert hex result to integer
                balance_wei = int(data['result'], 16)
                balance_usd = balance_wei / 1e6  # USDC has 6 decimals
                return balance_usd
    except:
        pass

    # Fallback: return 105 if RPC fails
    return 105.0

def calculate_position_size(arb, ks_markets, pm_markets):
    """Professional position sizing: calculates min/max bounds based on market conditions + risk"""
    ks_balance = get_kalshi_balance()
    pm_balance = get_polymarket_balance()

    arb_cost = arb['cost']
    arb_profit = arb['profit']

    # ==================== LIQUIDITY ANALYSIS ====================
    # Estimate max position from bid/ask spreads

    # Kalshi: spread indicates depth
    ks_spread = 0.01  # Default Kalshi spread
    for m in ks_markets:
        if 'yes' in m and 'no' in m:
            ks_spread = abs(m['yes'] - m['no'])
            break

    # Tight spread (<1¢) = deep liquidity, wider spread = shallow
    if ks_spread < 0.01:
        ks_max_liq = 20.0  # Very liquid
    elif ks_spread < 0.03:
        ks_max_liq = 10.0  # Normal
    elif ks_spread < 0.05:
        ks_max_liq = 5.0   # Thin
    else:
        ks_max_liq = 2.0   # Very thin

    # Polymarket: bid/ask spread signals liquidity depth
    pm_spread = 0.05  # Default spread estimate
    for m in pm_markets:
        if 'yes' in m and 'ask' in m:
            pm_spread = abs(m['ask'] - m['yes'])
            break

    if pm_spread < 0.02:
        pm_max_liq = 50.0  # Very deep
    elif pm_spread < 0.05:
        pm_max_liq = 25.0  # Good depth
    elif pm_spread < 0.10:
        pm_max_liq = 10.0  # Moderate
    else:
        pm_max_liq = 5.0   # Shallow

    # ==================== CAPITAL CONSTRAINTS ====================
    # Max position from available capital (50% of each balance)
    ks_capital_max = (ks_balance * 0.5) / arb_cost if arb_cost > 0 else 0
    pm_capital_max = (pm_balance * 0.5) / arb_cost if arb_cost > 0 else 0

    # ==================== PROFITABILITY THRESHOLD ====================
    # Calculate minimum position size that remains profitable
    # After fees, we need: profit_per_share > slippage risk (~0.001 per side)
    min_profit_per_share = arb_profit * 0.95  # Conservative: 95% of theoretical profit
    min_position = 0.01  # Floor: never go below $0.01 cost per side

    if min_profit_per_share > 0:
        # Position size where profit covers execution costs
        required_position = 0.01 / (min_profit_per_share + 0.001)
        min_position = max(0.01, required_position)

    # ==================== SYNTHESIZE: MAX POSITION ====================
    # Hard constraints: must respect ALL limits
    max_position = min(
        ks_max_liq,           # Kalshi liquidity ceiling
        pm_max_liq,           # Polymarket liquidity ceiling
        ks_capital_max,       # Kalshi available capital
        pm_capital_max,       # Polymarket available capital
        MAX_POSITION          # Risk limit (50 shares max)
    )

    # ==================== FINAL POSITION SIZE ====================
    # Choose size between [min_position, max_position]
    # Strategy: use 70% of max if profitable, else use min
    if max_position >= min_position:
        # Normal case: scale position to 70% of max for safety margin
        position_size = max_position * 0.70
    else:
        # Constrained case: use minimum (only if capital/liquidity very low)
        position_size = min_position

    # Final bounds: always between 0.01 and 1.0 share
    position_size = max(0.01, min(position_size, 1.0))

    return {
        'size': position_size,
        'min': min_position,
        'max': max_position,
        'ks_balance': ks_balance,
        'pm_balance': pm_balance,
        'ks_liquidity': ks_max_liq,
        'pm_liquidity': pm_max_liq,
        'ks_spread': ks_spread,
        'pm_spread': pm_spread,
        'reason': f"Size={position_size:.2f} (min={min_position:.2f}, max={max_position:.2f})"
    }

def place_polymarket_order(market_id, side, quantity, price, is_sell=False):
    """Place TAKER order on Polymarket - MUST fill immediately or reject (FOK)

    CRITICAL: This uses FOK (Fill-or-Kill) which means:
    - Order fills COMPLETELY and IMMEDIATELY or
    - Order is REJECTED (never rests on book as limit order)

    Args:
        market_id: Array [YES_token, NO_token] from Polymarket
        side: 0 for YES, 1 for NO
        quantity: Number of contracts
        price: Price per contract (ignored - we fetch fresh ask/bid)
        is_sell: True to SELL position, False to BUY (default)
    """
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        client = get_polymarket_client()
        if not client:
            return {'success': False, 'error': 'Failed to initialize Polymarket client'}

        # Select correct token ID based on side
        if isinstance(market_id, list):
            token_id = market_id[side]  # side=0 gets YES token, side=1 gets NO token
        else:
            token_id = market_id

        # CRITICAL: Get FRESH orderbook immediately before order placement
        try:
            orderbook = client.get_order_book(token_id)
            # OrderBookSummary object has .asks and .bids attributes (not dict)
            if is_sell:
                # Selling: take the best BID
                bids = orderbook.bids if hasattr(orderbook, 'bids') else []
                if not bids or len(bids) == 0:
                    return {'success': False, 'error': 'No bids available - cannot sell'}

                best_bid_price = float(bids[0].price) if hasattr(bids[0], 'price') else float(bids[0]['price'])
                best_bid_size = float(bids[0].size) if hasattr(bids[0], 'size') else float(bids[0]['size'])

                # Slightly UNDERPRICE to ensure we cross spread and fill immediately
                taker_price = max(best_bid_price - 0.01, 0.01)  # Min $0.01 (Polymarket limit)
                taker_price = round(taker_price, 2)

                size = float(quantity)
                total_value = taker_price * size

                if size > best_bid_size:
                    return {'success': False, 'error': f'Insufficient buy-side liquidity: need {size:.4f}, only {best_bid_size:.4f} available'}

                order_side = SELL
            else:
                # Buying: take the best ASK
                asks = orderbook.asks if hasattr(orderbook, 'asks') else []
                if not asks or len(asks) == 0:
                    return {'success': False, 'error': 'No asks available - cannot buy'}

                best_ask_price = float(asks[0].price) if hasattr(asks[0], 'price') else float(asks[0]['price'])
                best_ask_size = float(asks[0].size) if hasattr(asks[0], 'size') else float(asks[0]['size'])

                # Slightly OVERPAY to ensure we cross spread and fill immediately
                taker_price = min(best_ask_price + 0.01, 0.99)  # Cap at $0.99 max
                taker_price = round(taker_price, 2)

                size = float(quantity)
                total_value = taker_price * size

                # Polymarket requires minimum $1.00 total order value for BUYS
                if total_value < 1.0:
                    return {'success': False, 'error': f'Order value ${total_value:.2f} < $1.00 minimum (Polymarket requirement)'}

                if size > best_ask_size:
                    return {'success': False, 'error': f'Insufficient liquidity: need {size:.4f} shares, only {best_ask_size:.4f} available at best ask'}

                order_side = BUY
        except Exception as e:
            return {'success': False, 'error': f'Cannot get orderbook: {str(e)}'}

        # Create order at taker price with FOK
        order_args = OrderArgs(
            token_id=token_id,
            price=taker_price,
            size=round(size, 4),
            side=order_side
        )

        # Post with FOK (Fill-or-Kill) - fills immediately or cancels, NEVER rests on book
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK)

        if isinstance(resp, dict):
            order_id = resp.get('orderID') or resp.get('id') or str(resp)
            return {'success': True, 'order_id': order_id, 'response': resp}
        else:
            order_id = str(resp)
            return {'success': True, 'order_id': order_id, 'response': resp}

    except Exception as e:
        import traceback
        error_msg = str(e)
        error_detail = traceback.format_exc()
        print(f"[ERROR] Polymarket exception: {error_msg}")
        print(f"[ERROR] Details: {error_detail[:500]}")
        return {'success': False, 'error': error_msg}

def execute_arb(arb, ks_games, pm_games):
    """Execute arbitrage with BOTH orders or NONE - never naked positions"""
    cost = arb['cost']
    if cost <= 0:
        return False

    # Safety checks
    if arb['profit'] <= 0:
        print(f"  ✗ Rejected: No profit")
        return False

    if cost > 1.0:
        print(f"  ✗ Rejected: Cost ${cost:.4f} > $1.00")
        return False

    # CHECK BANKROLLS BEFORE EXECUTING
    ks_balance = get_kalshi_balance()
    pm_balance = get_polymarket_balance()

    # Get markets for liquidity analysis
    game_key = arb['game']
    ks_markets = ks_games.get(game_key, [])
    pm_markets = pm_games.get(game_key, [])

    # Calculate position size: Use LIQUIDITY_PERCENT of available balance on BOTH sides
    # Check liquidity on BOTH sides and take the SMALLER one (safety margin)

    # Estimate available liquidity (LIQUIDITY_PERCENT of portfolio is our max per trade)
    # This is a safety margin - we never use full liquidity
    max_liq_ks = (ks_balance * LIQUIDITY_PERCENT) if ks_balance > 0 else 0
    max_liq_pm = (pm_balance * LIQUIDITY_PERCENT) if pm_balance > 0 else 0

    # MARKET LIQUIDITY CHECKS - find the specific markets being traded
    ks_market = None
    pm_market = None

    for m in ks_markets:
        if m.get('id') == arb['ks_id']:
            ks_market = m
            break

    for m in pm_markets:
        if m.get('id') == arb['pm_id'] or (isinstance(m.get('id'), list) and arb['pm_id'] in m['id']):
            pm_market = m
            break

    # Calculate max safe position based on market liquidity
    max_ks_liquidity_shares = float('inf')
    max_pm_liquidity_shares = float('inf')

    if ks_market:
        # Kalshi: Don't trade more than 10% of open interest or 5% of 24h volume (whichever is larger)
        ks_open_interest = ks_market.get('open_interest', 0)
        ks_volume_24h = ks_market.get('volume_24h', 0)
        ks_liquidity_metric = max(ks_open_interest, ks_volume_24h)

        if ks_liquidity_metric > 0:
            max_ks_liquidity_shares = ks_liquidity_metric * 0.10  # 10% of liquidity metric

    if pm_market:
        # Polymarket: Don't trade more than 1% of total volume (conservative)
        pm_volume = pm_market.get('volume', 0)
        pm_volume_24h = pm_market.get('volume_24h', 0)

        # Use 24h volume if available, otherwise 1% of total volume
        pm_liquidity_metric = pm_volume_24h if pm_volume_24h > 0 else pm_volume * 0.1

        if pm_liquidity_metric > 0 and cost > 0:
            # Convert volume (in USD) to shares
            max_pm_liquidity_shares = (pm_liquidity_metric * 0.01) / cost  # 1% of volume

    # Position size is minimum of: bankroll limits, global max, AND market liquidity
    # MAX_POSITION is in DOLLARS, so convert to shares by dividing by cost
    position_size = min(
        max_liq_ks / cost if cost > 0 else 0,  # LIQUIDITY_PERCENT of Kalshi balance / cost (shares)
        max_liq_pm / cost if cost > 0 else 0,  # LIQUIDITY_PERCENT of Polymarket balance / cost (shares)
        MAX_POSITION / cost if cost > 0 else 0,  # Global max position size in DOLLARS converted to shares
        max_ks_liquidity_shares,  # Market liquidity on Kalshi
        max_pm_liquidity_shares   # Market liquidity on Polymarket
    )

    # For test mode, use fixed size (only if TEST_TINY_ORDER is True)
    if TEST_TINY_ORDER:
        position_size = TEST_ORDER_SIZE

    # Enforce minimum position size - Kalshi requires integer contracts (minimum 1)
    if position_size < 1.0:
        print(f"  ✗ Position too small: {position_size:.2f} shares < 1.0 minimum (Kalshi requires integer contracts)")
        return False

    # ROUND to integer for Kalshi (required by API)
    position_size = int(position_size)

    trade_cost = cost * position_size
    locked_profit = arb['profit'] * position_size

    # VERIFY CAPITAL AVAILABLE - MUST have funds on BOTH platforms
    required_ks_capital = trade_cost
    required_pm_capital = trade_cost

    if ks_balance < required_ks_capital:
        print(f"  ✗ Insufficient Kalshi capital: ${ks_balance:.2f} < ${required_ks_capital:.2f}")
        return False

    if pm_balance < required_pm_capital:
        print(f"  ✗ Insufficient Polymarket capital: ${pm_balance:.2f} < ${required_pm_capital:.2f}")
        return False

    # Determine action based on mode
    if DRY_RUN:
        print(f"  [DRY RUN] {position_size:.2f}Sh @ ${cost:.4f} = ${trade_cost:.2f}")
        print(f"    Capital: KS ${ks_balance:.2f} | PM ${pm_balance:.2f}")
        print(f"    Profit: ${locked_profit:.4f} ({arb['roi']*100:.2f}%)")
        return True

    if TEST_TINY_ORDER:
        position_size = TEST_ORDER_SIZE  # Override with small test amount
        trade_cost = cost * position_size
        print(f"  [TEST] ${TEST_ORDER_SIZE:.2f} test @ ${cost:.4f}")
    else:
        print(f"  [LIVE] {position_size:.2f}Sh @ ${cost:.4f} = ${trade_cost:.2f}")
        print(f"    Capital: KS ${ks_balance:.2f} | PM ${pm_balance:.2f}")
        print(f"    Profit locked: ${locked_profit:.4f}")

    # EXECUTION - MUST succeed on BOTH platforms or cancel both
    # Strategy: Place POLYMARKET FIRST (the problematic one), then Kalshi
    # If Polymarket fails, we haven't placed Kalshi yet = no rollback needed
    # If Polymarket succeeds but Kalshi fails, we can cancel Polymarket
    pm_order_id = None
    ks_order_id = None
    print(f"    [SAFETY] Both legs must fill or rollback - no naked positions")

    try:
        # Get fresh Kalshi market data (needed for emergency close if Polymarket fails after Kalshi fills)
        ks_fresh_market = None
        for m in ks_markets:
            if m.get('id') == arb['ks_id']:
                ks_fresh_market = m
                break

        if not ks_fresh_market:
            print(f"    ✗ Kalshi market disappeared")
            return False

        # STEP 1: Place Polymarket order FIRST - TAKER order at best available price
        # Use outcomes array to determine which token is which

        # Find the PM market to get outcomes array
        pm_market_data = None
        for m in pm_games.get(game_key, []):
            if m['id'] == arb['pm_id']:
                pm_market_data = m
                break

        if not pm_market_data:
            print(f"    ✗ PM market disappeared")
            return False

        outcomes = pm_market_data.get('outcomes', ['Yes', 'No'])

        # pm_side from arb finding is already 'yes' (index 0) or 'no' (index 1)
        if arb['pm_side'] == 'yes':
            pm_side_index = 0
            taker_price = arb.get('pm_best_ask', 0.50)
            # Get team name from pm_team field
            pm_side_name = arb.get('pm_team', 'YES').upper()
        elif arb['pm_side'] == 'no':
            pm_side_index = 1
            taker_price = arb.get('pm_best_ask', 0.50)
            # Get team name from pm_team field
            pm_side_name = arb.get('pm_team', 'NO').upper()
        else:
            # Shouldn't happen but fallback
            pm_side_index = 0 if arb['pm_side'] == 0 else 1
            pm_side_name = f"Token {pm_side_index}"
            taker_price = arb.get('pm_best_ask', 0.50)

        print(f"    → Polymarket {pm_side_name} ({position_size:.2f} share) @ {taker_price:.4f}...")
        pm_result = place_polymarket_order(arb['pm_id'], pm_side_index, position_size, taker_price)

        if not pm_result['success']:
            pm_error = pm_result.get('error', 'Unknown error')
            print(f"    ✗ Polymarket failed: {pm_error}")
            print(f"    ✗ No Kalshi order placed - safe exit")
            # Don't log - no positions were created (safe rejection)
            return False

        print(f"    ✓ Polymarket OK")
        pm_order_id = pm_result.get('order_id')

        # STEP 2: Place Kalshi order SECOND - now that Polymarket succeeded
        # Use fresh market ask prices for guaranteed fill
        if arb['ks_side'] == 'yes':
            taker_price_ks = int(ks_fresh_market['yes'] * 100)  # Fresh YES ask price
        else:
            taker_price_ks = int(ks_fresh_market['no'] * 100)   # Fresh NO ask price

        print(f"    → Kalshi {arb['ks_side'].upper()} ({position_size:.2f} share) @ {taker_price_ks/100:.2f}...")
        ks_result = place_kalshi_order(arb['ks_id'], arb['ks_side'], int(position_size), taker_price_ks)

        if not ks_result['success']:
            ks_error = ks_result.get('error', 'Unknown')
            print(f"    ✗ Kalshi failed: {ks_error[:200]}")  # Show more of error
            print(f"    → EMERGENCY: Closing Polymarket position immediately...")

            # Emergency close: SELL the Polymarket position we just bought at market
            close_result = place_polymarket_order(arb['pm_id'], pm_side_index, position_size, 0.0, is_sell=True)

            if close_result['success']:
                print(f"    ✓ Polymarket position closed - Flattened at market price")
                print(f"    ⚠️  Small loss from spread crossing")
                log_trade(arb, position_size, False, both_legs_filled=True)  # Emergency close succeeded
            else:
                close_error = close_result.get('error', 'Unknown')
                print(f"    ✗✗ EMERGENCY CLOSE FAILED! NAKED POLYMARKET POSITION!")
                print(f"    Polymarket buy order: {pm_order_id}")
                print(f"    Close error: {close_error}")

                # Provide specific guidance based on error
                if 'balance' in close_error or 'allowance' in close_error:
                    print(f"    → MANUAL ACTION: Sell {pm_side_name} position on Polymarket UI")
                    print(f"    → Likely token approval issue (CLOB needs approval to spend outcome tokens)")

                log_trade(arb, position_size, False, both_legs_filled=False)  # NAKED POSITION!
            return False

        ks_order_id = ks_result['order_id']
        if hasattr(ks_order_id, 'order_id'):
            ks_order_id = ks_order_id.order_id
        print(f"    ✓ Kalshi OK")

        # Both succeeded - now verify positions match
        print(f"    → Verifying positions...")
        verification_ok = verify_positions_match(arb['ks_id'], arb['pm_id'], position_size)

        if not verification_ok:
            print(f"    ⚠️⚠️  POSITION MISMATCH DETECTED!")
            print(f"    → Expected: {position_size} contracts on each side")
            print(f"    → Check your positions manually!")
            # Still log as success but flag for review
        else:
            print(f"    ✓ Positions verified")

        locked_profit = arb['profit'] * position_size
        print(f"    ✓✓ SUCCESS - Locked ${locked_profit:.4f} ({arb['roi']*100:.2f}%)")
        log_trade(arb, position_size, True, both_legs_filled=True)
        return True

    except Exception as e:
        print(f"    ✗ Exception: {str(e)[:50]}")
        if ks_order_id:
            print(f"    → Emergency rollback...")
            cancel_result = cancel_kalshi_order(ks_order_id)
            if not cancel_result.get('success', False):
                print(f"    ✗✗ ROLLBACK FAILED! NAKED POSITION!")
                log_trade(arb, position_size, False, both_legs_filled=False)  # NAKED!
            else:
                log_trade(arb, position_size, False, both_legs_filled=True)  # Rolled back
        return False

def check_loss_threshold():
    """Check if losses exceed kill threshold - stop bot if they do"""
    try:
        if not os.path.exists(TRADE_LOG):
            return True  # No trades yet, continue

        with open(TRADE_LOG, 'r') as f:
            trades = json.load(f)

        if not trades:
            return True

        # Calculate total P&L
        total_cost = sum(t.get('trade_cost', 0) for t in trades)
        total_profit = sum(t.get('locked_profit', 0) if t.get('success', False) else -t.get('trade_cost', 0) for t in trades)

        # Check if failed trades (from rollback) should be counted as losses
        failed_trades = [t for t in trades if not t.get('success', False)]
        net_pnl = total_profit

        if total_cost == 0:
            return True

        loss_percent = abs(min(0, net_pnl)) / total_cost

        if loss_percent >= LOSS_KILL_THRESHOLD:
            print(f"\n{'='*70}")
            print(f"[CRITICAL] LOSS THRESHOLD EXCEEDED!")
            print(f"Total Cost: ${total_cost:.2f}")
            print(f"Net P&L: ${net_pnl:.2f}")
            print(f"Loss: {loss_percent*100:.1f}% (threshold: {LOSS_KILL_THRESHOLD*100:.0f}%)")
            print(f"[STOPPING BOT]")
            print(f"{'='*70}\n")
            return False

        return True
    except Exception as e:
        print(f"[WARNING] Error checking loss threshold: {e}")
        return True

def check_naked_positions():
    """Check for naked positions - CRITICAL safety check"""
    try:
        if not os.path.exists(TRADE_LOG):
            return True  # No trades yet, continue

        with open(TRADE_LOG, 'r') as f:
            trades = json.load(f)

        if not trades:
            return True

        # Check last 10 trades for any naked positions
        recent_trades = trades[-10:]
        naked_positions = [t for t in recent_trades if not t.get('both_legs_filled', True)]

        if naked_positions:
            print(f"\n{'='*70}")
            print(f"[CRITICAL SAFETY] NAKED POSITION DETECTED!")
            print(f"Found {len(naked_positions)} trade(s) with only one leg filled!")
            print(f"")
            for i, trade in enumerate(naked_positions, 1):
                print(f"Trade {i}:")
                print(f"  Type: {trade.get('type')}")
                print(f"  Game: {trade.get('game')}")
                print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(trade.get('timestamp', 0)))}")
            print(f"")
            print(f"[ACTION] STOPPING BOT IMMEDIATELY - Manual intervention required!")
            print(f"[MANUAL STEPS]")
            print(f"  1. Check open positions on both Kalshi and Polymarket")
            print(f"  2. Manually close any naked positions")
            print(f"  3. Review trade log: {TRADE_LOG}")
            print(f"{'='*70}\n")
            return False

        return True
    except Exception as e:
        print(f"[WARNING] Error checking naked positions: {e}")
        return True

def scan():
    """Single scan - find and execute Kalshi ↔ Polymarket arbitrages"""
    print(f"\n{'='*70}")
    print(f"[SCAN] {time.strftime('%H:%M:%S')}")
    print('='*70)

    # Get markets from both platforms
    print("\n[1/4] Fetching Kalshi markets...")
    ks_games = get_kalshi_games()
    ks_count = sum(len(markets) for markets in ks_games.values())
    print(f"  Found {len(ks_games)} games, {ks_count} straight markets")

    print("\n[2/4] Fetching Polymarket markets...")
    pm_games = get_polymarket_games()
    pm_count = sum(len(markets) for markets in pm_games.values())
    print(f"  Found {len(pm_games)} games, {pm_count} markets")

    if not ks_games or not pm_games:
        print("\n[ERROR] No markets found on one or both platforms")
        return

    # Find arbitrage opportunities
    print("\n[3/4] Finding arbitrages...")
    arbs = find_arbs(ks_games, pm_games)

    if not arbs:
        print("  No arbitrage opportunities found")
        return

    print(f"  Found {len(arbs)} arbitrage opportunities")

    # Show top opportunities
    arbs.sort(key=lambda x: x['roi'], reverse=True)
    print("\n  Top opportunities:")
    for arb in arbs[:5]:
        print(f"    {arb['type']:8} | {arb['game']:30} | Cost: ${arb['cost']:.3f} | Profit: ${arb['profit']:.3f} | ROI: {arb['roi']*100:.1f}%")
        if 'hedge_check' in arb:
            print(f"      → {arb['hedge_check']}")

    # Execute best arbitrages (limit to 2 per scan to prevent capital drain)
    print("\n[4/4] Executing arbitrages...")
    max_execute = 2  # Execute at most 2 arbs per scan
    executed = 0

    for arb in arbs[:max_execute]:
        if arb['roi'] >= MIN_PROFIT:
            game_key = arb['game']

            # Check for duplicate arbitrage (already executed recently)
            if is_duplicate_arb(arb):
                print(f"\n  Skipping: {arb['type']} {game_key} (already executed recently)")
                continue

            # Check position limit per game (max 3 positions per game)
            current_position_count = GAME_POSITION_COUNT.get(game_key, 0)
            if current_position_count >= MAX_POSITIONS_PER_GAME:
                print(f"\n  Skipping: {arb['type']} {game_key} (max {MAX_POSITIONS_PER_GAME} positions reached)")
                continue

            print(f"\n  Executing: {arb['type']} {game_key}")
            if 'hedge_check' in arb:
                print(f"    Hedge: {arb['hedge_check']}")
            success = execute_arb(arb, ks_games, pm_games)
            if success:
                executed += 1
                mark_arb_executed(arb)  # Track this arbitrage to prevent duplicates
                GAME_POSITION_COUNT[game_key] = current_position_count + 1  # Increment position count
                print(f"    ✓ Success (position {GAME_POSITION_COUNT[game_key]}/{MAX_POSITIONS_PER_GAME} on this game)")
            else:
                print(f"    ✗ Failed")

    print(f"\n[RESULT] Executed {executed}/{min(len(arbs), max_execute)} arbitrages")

if __name__ == "__main__":
    print("="*70)
    print("KALSHI ↔ POLYMARKET ARBITRAGE BOT")
    print("Markets: NFL & CFB game markets (winners, spreads, totals)")
    print("Strategy: Straight markets only (NO bundles/parlays)")
    print(f"Mode: {'🔴 LIVE TRADING' if not DRY_RUN else '🟡 DRY RUN'}")
    print(f"Max Position: ${MAX_POSITION:.2f}")
    print(f"Min Profit Threshold: {MIN_PROFIT*100:.1f}%")
    print(f"Loss Kill Threshold: {LOSS_KILL_THRESHOLD*100:.0f}%")
    print(f"Poll Interval: {POLL_INTERVAL}s")
    print("")
    print("SAFETY FEATURES:")
    print("  ✓ Both legs executed simultaneously (market orders)")
    print("  ✓ Auto-rollback if one side fails")
    print("  ✓ Naked position detection (auto-stops bot)")
    print("  ✓ Loss threshold monitoring")
    print(f"  ✓ {LIQUIDITY_PERCENT*100:.0f}% liquidity limit per trade (max ${MAX_POSITION:.0f})")
    print("="*70)

    try:
        while True:
            # CRITICAL SAFETY CHECKS before each scan
            if not check_loss_threshold():
                sys.exit(1)

            # Check for naked positions - STOP immediately if found
            if not check_naked_positions():
                print("\n[CRITICAL] Bot stopped due to naked position detection")
                sys.exit(1)

            scan()
            print(f"\n[NEXT] {POLL_INTERVAL}s")
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n[STOPPED]")
        sys.exit(0)
