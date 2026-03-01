import requests
import json
import os
from pathlib import Path
import sys

# Add root to path
sys.path.insert(0, str(Path(__file__).parent.resolve()))
import config

def debug_market_keys():
    # Load API key
    try:
        with open(config.SIMMER_API_KEY_PATH) as f:
            api_key = json.load(f).get("api_key")
    except Exception:
        print("API key not found")
        return

    headers = {"Authorization": f"Bearer {api_key}"}
    
    # 1. Check full markets list
    print("Fetching /api/sdk/markets...")
    resp = requests.get(
        f"{config.SIMMER_BASE_URL}/api/sdk/markets",
        headers=headers,
        params={"status": "active", "limit": 10},
        timeout=10
    )
    
    if resp.status_code == 200:
        markets = resp.json()
        if isinstance(markets, dict):
            markets = markets.get("markets", [])
            
        if markets:
            m = markets[0]
            print(f"Keys in market object: {list(m.keys())}")
            print(f"best_bid: {m.get('best_bid')}")
            print(f"best_ask: {m.get('best_ask')}")
            
            # Check for common variants
            for k in ['bid', 'ask', 'buy', 'sell', 'min_ask', 'max_bid']:
                if k in m:
                    print(f"Found alternative key '{k}': {m[k]}")
                    
            # 2. Check context for the same market
            mid = m.get("id") or m.get("market_id")
            if mid:
                print(f"\nFetching /api/sdk/context/{mid}...")
                c_resp = requests.get(
                    f"{config.SIMMER_BASE_URL}/api/sdk/context/{mid}",
                    headers=headers,
                    timeout=10
                )
                if c_resp.status_code == 200:
                    ctx = c_resp.json()
                    print(f"Keys in context object: {list(ctx.keys())}")
                    
                    if 'market' in ctx:
                        print(f"Keys in ctx['market']: {list(ctx['market'].keys())}")
                        for k in ['best_bid', 'best_ask', 'bid', 'ask']:
                            if k in ctx['market']:
                                print(f"Found in ctx['market'] '{k}': {ctx['market'][k]}")
                                
                    if 'slippage' in ctx:
                        print(f"Keys in ctx['slippage']: {list(ctx['slippage'].keys())}")
                        print(f"Slippage data: {ctx['slippage']}")

                    if 'edge' in ctx:
                        print(f"Keys in ctx['edge']: {list(ctx['edge'].keys())}")
                        print(f"Edge data: {ctx['edge']}")

if __name__ == "__main__":
    debug_market_keys()
