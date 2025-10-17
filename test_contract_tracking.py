#!/usr/bin/env python3
"""
Simple test to verify contract tracking WebSocket functionality
"""

import requests
import json
import time
import sys

def test_contract_tracking():
    base_url = "https://derivbot-upgrade.preview.emergentagent.com/api"
    
    print("🔍 Testing Contract Tracking WebSocket Functionality")
    print(f"   Base URL: {base_url}")
    
    # Step 1: Get baseline
    print("\n📊 Step 1: Getting baseline metrics")
    response = requests.get(f"{base_url}/strategy/status", timeout=10)
    if response.status_code != 200:
        print(f"❌ Failed to get baseline: {response.status_code}")
        return False
    
    baseline = response.json()
    print(f"   Baseline total_trades: {baseline.get('total_trades', 0)}")
    print(f"   Baseline daily_pnl: {baseline.get('daily_pnl', 0.0)}")
    
    # Step 2: Make a buy with shorter duration (1 tick)
    print("\n💰 Step 2: Making a buy with 1 tick duration")
    buy_payload = {
        "type": "CALLPUT",
        "symbol": "R_10",
        "contract_type": "CALL",
        "duration": 1,  # Just 1 tick for faster expiration
        "duration_unit": "t",
        "stake": 1,
        "currency": "USD"
    }
    
    response = requests.post(f"{base_url}/deriv/buy", json=buy_payload, timeout=20)
    if response.status_code != 200:
        print(f"❌ Buy failed: {response.status_code} - {response.text}")
        return False
    
    buy_result = response.json()
    contract_id = buy_result.get('contract_id')
    print(f"   ✅ Buy successful: Contract ID {contract_id}")
    print(f"   Buy price: {buy_result.get('buy_price')}")
    print(f"   Payout: {buy_result.get('payout')}")
    
    # Step 3: Monitor for shorter time (1 minute max for 1 tick)
    print(f"\n⏳ Step 3: Monitoring contract {contract_id} for up to 60 seconds")
    
    max_wait = 60
    check_interval = 5
    elapsed = 0
    
    while elapsed < max_wait:
        print(f"   ⏱️  Checking at t+{elapsed}s...")
        
        response = requests.get(f"{base_url}/strategy/status", timeout=10)
        if response.status_code == 200:
            current = response.json()
            current_total = current.get('total_trades', 0)
            current_pnl = current.get('daily_pnl', 0.0)
            
            print(f"      Total trades: {current_total} (baseline: {baseline.get('total_trades', 0)})")
            print(f"      Daily PnL: {current_pnl} (baseline: {baseline.get('daily_pnl', 0.0)})")
            
            if current_total > baseline.get('total_trades', 0):
                print(f"   ✅ SUCCESS! Metrics updated after {elapsed} seconds")
                print(f"      Total trades increased by {current_total - baseline.get('total_trades', 0)}")
                print(f"      PnL change: {current_pnl - baseline.get('daily_pnl', 0.0):+.2f}")
                return True
        
        time.sleep(check_interval)
        elapsed += check_interval
    
    print(f"   ❌ TIMEOUT: No metrics update after {max_wait} seconds")
    return False

if __name__ == "__main__":
    success = test_contract_tracking()
    sys.exit(0 if success else 1)