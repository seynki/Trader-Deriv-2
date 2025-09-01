#!/usr/bin/env python3
"""
Test to verify if backend is processing WebSocket messages properly
"""

import requests
import json
import time
import asyncio

async def test_backend_ws_processing():
    """Test if backend processes WebSocket messages by making a new contract and monitoring"""
    print("🔍 Testing Backend WebSocket Message Processing")
    
    base_url = "http://localhost:8001/api"
    
    # Step 1: Check initial status
    print("\n📊 Step 1: Checking initial backend status")
    response = requests.get(f"{base_url}/deriv/status", timeout=10)
    if response.status_code != 200:
        print(f"❌ Backend status failed: {response.status_code}")
        return False
    
    status = response.json()
    print(f"   Connected: {status.get('connected')}")
    print(f"   Authenticated: {status.get('authenticated')}")
    print(f"   Last heartbeat: {status.get('last_heartbeat')}")
    
    # Step 2: Get baseline metrics
    response = requests.get(f"{base_url}/strategy/status", timeout=10)
    if response.status_code != 200:
        print(f"❌ Strategy status failed: {response.status_code}")
        return False
    
    baseline = response.json()
    baseline_total = baseline.get('total_trades', 0)
    baseline_pnl = baseline.get('daily_pnl', 0.0)
    
    print(f"\n📊 Baseline metrics:")
    print(f"   Total trades: {baseline_total}")
    print(f"   Daily PnL: {baseline_pnl}")
    
    # Step 3: Make a new contract with very short duration
    print(f"\n💰 Step 3: Creating new contract with 1 tick duration")
    buy_payload = {
        "type": "CALLPUT",
        "symbol": "R_10",
        "contract_type": "CALL",
        "duration": 1,
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
    print(f"   ✅ New contract created: {contract_id}")
    print(f"   Buy price: {buy_result.get('buy_price')}")
    print(f"   Payout: {buy_result.get('payout')}")
    
    # Step 4: Monitor backend status for heartbeat updates
    print(f"\n⏳ Step 4: Monitoring backend for 60 seconds...")
    print("   Checking for heartbeat updates and metric changes...")
    
    start_time = time.time()
    last_heartbeat = status.get('last_heartbeat')
    heartbeat_updated = False
    metrics_updated = False
    
    while time.time() - start_time < 60:
        elapsed = int(time.time() - start_time)
        
        # Check backend status
        response = requests.get(f"{base_url}/deriv/status", timeout=5)
        if response.status_code == 200:
            current_status = response.json()
            current_heartbeat = current_status.get('last_heartbeat')
            
            if current_heartbeat != last_heartbeat and current_heartbeat is not None:
                if not heartbeat_updated:
                    print(f"   ✅ Heartbeat updated at t+{elapsed}s: {current_heartbeat}")
                    heartbeat_updated = True
                last_heartbeat = current_heartbeat
        
        # Check metrics
        response = requests.get(f"{base_url}/strategy/status", timeout=5)
        if response.status_code == 200:
            current_metrics = response.json()
            current_total = current_metrics.get('total_trades', 0)
            current_pnl = current_metrics.get('daily_pnl', 0.0)
            
            if current_total > baseline_total:
                print(f"   ✅ Metrics updated at t+{elapsed}s!")
                print(f"      Total trades: {current_total} (+{current_total - baseline_total})")
                print(f"      Daily PnL: {current_pnl} (change: {current_pnl - baseline_pnl:+.2f})")
                metrics_updated = True
                break
        
        if elapsed % 10 == 0:
            print(f"   ⏱️  t+{elapsed}s - Heartbeat: {'✅' if heartbeat_updated else '❌'}, Metrics: {'✅' if metrics_updated else '❌'}")
        
        time.sleep(2)
    
    print(f"\n📊 Test Results:")
    print(f"   Heartbeat Updates: {'✅' if heartbeat_updated else '❌'}")
    print(f"   Metrics Updates: {'✅' if metrics_updated else '❌'}")
    
    if heartbeat_updated and not metrics_updated:
        print("\n🔍 Analysis: Backend WebSocket is receiving messages (heartbeat works)")
        print("   but contract tracking/stats update is not working properly")
    elif not heartbeat_updated and not metrics_updated:
        print("\n🔍 Analysis: Backend WebSocket is not receiving messages properly")
        print("   (no heartbeat updates detected)")
    elif heartbeat_updated and metrics_updated:
        print("\n🔍 Analysis: Everything is working correctly!")
    else:
        print("\n🔍 Analysis: Metrics updated without heartbeat - unusual case")
    
    return metrics_updated

if __name__ == "__main__":
    asyncio.run(test_backend_ws_processing())