"""
Phase 2: Account Setup & Connection Verification
1. Checks Simmer registration and API key
2. Checks claim status
3. Verifies trading access
4. Places a test trade
5. Discovers active BTC 5-min up/down market
6. Saves results to setup_log.md
"""

import json
import requests
import sys
from pathlib import Path
from datetime import datetime
import config

BASE = config.SIMMER_BASE_URL


def load_api_key():
    try:
        with open(config.SIMMER_API_KEY_PATH) as f:
            return json.load(f).get("api_key")
    except FileNotFoundError:
        return None


def check_agent_status(api_key):
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.get(f"{BASE}/api/sdk/agents/me", headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        print(f"Error getting agent status: {resp.status_code} {resp.text[:200]}")
        return None
    except Exception as e:
        print(f"Exception getting agent status: {e}")
        return None


def get_markets(api_key, limit=100):
    """Fetch active markets. Returns a list."""
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.get(
        f"{BASE}/api/sdk/markets",
        headers=headers,
        params={"status": "active", "limit": limit},
        timeout=15,
    )
    if resp.status_code != 200:
        return []
    data = resp.json()
    # API returns a list directly
    if isinstance(data, list):
        return data
    # Or it might be nested
    return data.get("markets", data.get("results", []))


def discover_markets(api_key, min_window=None, max_window=None):
    """
    Find active crypto markets within the specified time window.
    Implements the sweet spot strategy: 1h to 3d.
    """
    if min_window is None:
        min_window = config.MARKET_FILTER["min_window_seconds"]
    if max_window is None:
        max_window = config.MARKET_FILTER["max_window_seconds"]
        
    print(f"🔎 Discovering markets in window: {min_window/3600:.1f}h - {max_window/3600:.1f}h")
    
    all_markets = get_markets(api_key, limit=200) # Aumentar limite para achar mais opções
    valid_markets = []
    
    now = datetime.utcnow()
    
    for m in all_markets:
        # 1. Filtro de Ativo (Crypto)
        q = m.get("question", "").lower()
        is_crypto = any(k in q for k in config.TARGET_MARKET_QUERIES)
        is_updown = "up or down" in q or "up/down" in q
        
        if not (is_crypto and is_updown):
            continue
            
        # 2. Filtro de Tempo (Sweet Spot)
        resolves_at_str = m.get("resolves_at")
        if not resolves_at_str:
            continue
            
        try:
            # Parse ISO date (handles Z and offsets)
            resolves_at = datetime.fromisoformat(resolves_at_str.replace("Z", "+00:00"))
            # Ensure UTC
            if resolves_at.tzinfo is not None:
                resolves_at = resolves_at.astimezone(None).replace(tzinfo=None) # Convert to naive UTC
                
            seconds_remaining = (resolves_at - now).total_seconds()
            
            # Filtro principal
            if min_window <= seconds_remaining <= max_window:
                # 3. Filtro de Liquidez/Spread (v3)
                # O arena.py e bots devem ter acesso a esses dados, mas setup.py roda isolado as vezes.
                # Se 'liquidity' ou 'spread' não estiverem no objeto 'm', não podemos filtrar aqui.
                # Mas assumindo que a API retorna 'liquidity' e podemos calcular spread.
                
                liquidity = float(m.get("liquidity") or 0)
                if liquidity < config.MARKET_FILTER["min_liquidity_usd"]:
                     # print(f"Skipping {mid}: Low liquidity ${liquidity:.0f}")
                     continue
                     
                # Spread check (se bid/ask disponíveis)
                # A API retorna best_bid e best_ask?
                # Se não, o bot faz o check. Mas vamos tentar filtrar o grosso aqui.
                # best_bid = m.get("best_bid")
                # best_ask = m.get("best_ask")
                # if best_bid and best_ask:
                #     mid_price = (best_bid + best_ask) / 2
                #     if mid_price > 0:
                #         spread_pct = (best_ask - best_bid) / mid_price * 100
                #         if spread_pct > config.MARKET_FILTER["max_spread_percent"]:
                #             continue
                
                m["seconds_remaining"] = seconds_remaining
                valid_markets.append(m)
                
        except Exception as e:
            print(f"Error parsing date for {q}: {e}")
            continue
            
    print(f"✅ Found {len(valid_markets)} markets in the sweet spot.")
    
    # Fallback logic
    if not valid_markets and config.MARKET_FILTER["allow_fallback"]:
        fallback_min = config.MARKET_FILTER["fallback_min_seconds"]
        print(f"⚠️ No markets in sweet spot. Trying fallback window: > {fallback_min/60:.0f} mins")
        return discover_markets(api_key, min_window=fallback_min, max_window=max_window)
        
    # Sort by liquidity or time? Let's sort by time to expiration (soonest first within window)
    valid_markets.sort(key=lambda x: x["seconds_remaining"])
    
    return valid_markets

# Alias for compatibility
discover_btc_market = lambda api_key: discover_markets(api_key)[0] if discover_markets(api_key) else None


def place_test_trade(api_key):
    """Place a $1 SIM test trade to verify the pipeline."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    all_markets = get_markets(api_key, limit=10)
    if not all_markets:
        return {"success": False, "error": "No active markets"}

    test_market = all_markets[0]
    market_id = test_market.get("id") or test_market.get("market_id")
    question = test_market.get("question", "Unknown")

    print(f"\n   Test market: {question}")
    print(f"   Market ID: {market_id}")

    payload = {
        "market_id": market_id,
        "side": "yes",
        "amount": 1.0,
        "venue": "simmer",
        "source": "arena:setup-test",
        "reasoning": "Setup verification test trade",
    }

    print(f"   Placing: $1 $SIM on YES...")
    resp = requests.post(f"{BASE}/api/sdk/trade", headers=headers, json=payload, timeout=15)

    if resp.status_code in (200, 201):
        result = resp.json()
        print(f"   Trade successful!")
        return {"success": True, "trade_id": result.get("trade_id", result.get("id")), "market": question, "result": result}
    else:
        print(f"   Trade failed: {resp.status_code}")
        print(f"   Response: {resp.text[:300]}")
        return {"success": False, "error": f"{resp.status_code}: {resp.text[:200]}"}


def save_setup_log(agent_info, market, test_trade):
    log_path = Path(__file__).parent / "setup_log.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    market_id = (market.get("id") or market.get("market_id", "Not found")) if market else "Not found"
    market_q = market.get("question", "N/A") if market else "N/A"
    balance = agent_info.get("balance", "N/A")
    real_trading = agent_info.get("real_trading_enabled", False)

    log_content = f"""# Polymarket Bot Arena - Setup Log

**Generated:** {timestamp}

## Agent Status

- **Agent ID:** {agent_info.get('agent_id', 'N/A')}
- **Agent Name:** {agent_info.get('name', 'N/A')}
- **Status:** {agent_info.get('status', 'N/A')}
- **Claimed:** {agent_info.get('claimed', False)}
- **Real Trading Enabled:** {real_trading}
- **Balance ($SIM):** {balance}

## Target Market: BTC 5-Min Up/Down

- **Market ID:** {market_id}
- **Question:** {market_q}

## Test Trade

- **Success:** {test_trade.get('success', False)}
- **Trade ID:** {test_trade.get('trade_id', 'N/A')}
- **Market:** {test_trade.get('market', 'N/A')}
- **Error:** {test_trade.get('error', 'None')}

## Next Steps

"""
    if test_trade.get("success"):
        log_content += "Setup complete. Ready to run the arena in paper mode.\n"
    else:
        log_content += f"Test trade failed. Fix before proceeding.\nError: {test_trade.get('error', 'Unknown')}\n"

    log_path.write_text(log_content)
    print(f"\n   Setup log saved to: {log_path}")


def main():
    print("=" * 60)
    print("Polymarket Bot Arena - Setup")
    print("=" * 60)

    # 1. API key
    print("\n1. Checking Simmer API key...")
    api_key = load_api_key()
    if not api_key:
        print(f"   No API key at {config.SIMMER_API_KEY_PATH}")
        sys.exit(1)
    print(f"   API key loaded")

    # 2. Agent status
    print("\n2. Checking agent status...")
    agent_info = check_agent_status(api_key)
    if not agent_info:
        print("   Could not get agent status.")
        sys.exit(1)

    print(f"   Agent: {agent_info.get('name')} ({agent_info.get('agent_id')})")
    print(f"   Status: {agent_info.get('status')}")
    print(f"   Claimed: {agent_info.get('claimed')}")
    print(f"   Real trading: {agent_info.get('real_trading_enabled')}")
    print(f"   Balance: ${agent_info.get('balance', 0):,.2f} $SIM")

    # 3. Discover BTC market
    print("\n3. Discovering BTC 5-min up/down market...")
    market = discover_btc_market(api_key)

    # 4. Test trade
    print("\n4. Placing test trade...")
    test_trade = place_test_trade(api_key)

    # 5. Save log
    print("\n5. Saving setup log...")
    save_setup_log(agent_info, market, test_trade)

    print("\n" + "=" * 60)
    if test_trade.get("success"):
        print("SETUP COMPLETE — ready to run: python arena.py")
    else:
        print("SETUP INCOMPLETE — fix test trade first")
    print("=" * 60)

    return test_trade.get("success", False)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
