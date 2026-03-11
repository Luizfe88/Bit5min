import json
import requests
import os
import sys
from pathlib import Path

# Add current dir to path
sys.path.append(os.getcwd())
import config

def test_liquidity():
    # Load API Key
    api_key_path = config.SIMMER_API_KEY_PATH
    if not api_key_path.exists():
        print(f"API key not found at {api_key_path}")
        return

    with open(api_key_path) as f:
        api_key = json.load(f).get("api_key")

    if not api_key:
        print("API key not found in JSON.")
        return

    headers = {"Authorization": f"Bearer {api_key}"}
    
    print(f"--- Fetching active markets from Simmer ---")
    resp = requests.get(
        f"{config.SIMMER_BASE_URL}/api/sdk/markets",
        headers=headers,
        params={"status": "active", "limit": 100},
        timeout=15,
    )

    if resp.status_code != 200:
        print(f"Error fetching markets: {resp.status_code}")
        return

    markets_resp = resp.json()
    markets = markets_resp if isinstance(markets_resp, list) else markets_resp.get("markets", [])

    threshold = config.get_institutional_volume_threshold()
    print(f"Institutional Volume Threshold: ${threshold:,.2f}")
    print(f"{'QUESTION':<60} | {'LIQUIDITY/VOL':<12} | {'STATUS'}")
    print("-" * 88)

    passed_count = 0
    total_relevant = 0

    for m in markets:
        q = m.get("question", "").lower()
        
        # Simple relevance check (BTC/ETH/SOL)
        is_relevant = any(kw in q for kw in ["btc", "bitcoin", "eth", "ethereum", "sol", "solana"])
        if not is_relevant:
            continue
            
        total_relevant += 1
        
        # Try different fields
        liq = float(m.get("liquidity") or m.get("volume") or m.get("volume_24h") or 0)
        
        status = "[PASSED]" if liq >= threshold else "[FAILED]"
        if liq >= threshold:
            passed_count += 1
            
        print(f"{m.get('question')[:58]:<60} | ${liq:>10,.2f} | {status}")

    print("-" * 85)
    print(f"Summary: {passed_count}/{total_relevant} markets passed the liquidity threshold.")
    
    if passed_count == 0 and total_relevant > 0:
        print("\nWARNING: No relevant markets passed the threshold! This might be a very low-liquidity period.")
    elif passed_count > 0:
        print("\nSUCCESS: Found markets that meet the new requirement.")

if __name__ == "__main__":
    test_liquidity()
