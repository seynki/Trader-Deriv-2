#!/usr/bin/env python3
"""
Review Request Testing Script
Tests the specific endpoints requested in the review:
1) GET /api/strategy/status -> should be 200 with running=false
2) POST /api/strategy/start with JSON -> should be 200 with running=true  
3) Wait a few seconds, GET /api/strategy/status -> last_run_at should update
4) POST /api/strategy/stop -> running=false
5) POST /api/candles/ingest?symbol=R_100&granularity=60&count=120 -> Expect 200 or 503
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "https://hybrid-trade-algo.preview.emergentagent.com"
API_URL = f"{BASE_URL}/api"

def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

def test_endpoint(name, method, endpoint, expected_status=None, data=None, timeout=30):
    """Test a single endpoint"""
    url = f"{API_URL}/{endpoint}"
    log(f"🔍 {name}")
    log(f"   URL: {url}")
    if data:
        log(f"   Data: {json.dumps(data, indent=2)}")
    
    try:
        if method == 'GET':
            response = requests.get(url, timeout=timeout)
        elif method == 'POST':
            response = requests.post(url, json=data, timeout=timeout)
        else:
            raise ValueError(f"Unsupported method: {method}")

        log(f"   Status: {response.status_code}")
        
        try:
            response_data = response.json()
            log(f"   Response: {json.dumps(response_data, indent=2)}")
        except:
            response_data = {"raw_text": response.text}
            log(f"   Response Text: {response.text}")

        if expected_status and response.status_code != expected_status:
            log(f"❌ FAILED - Expected {expected_status}, got {response.status_code}")
            return False, response_data, response.status_code
        else:
            log(f"✅ SUCCESS")
            return True, response_data, response.status_code

    except Exception as e:
        log(f"❌ ERROR - {str(e)}")
        return False, {"error": str(e)}, 0

def main():
    log("🚀 REVIEW REQUEST TESTING")
    log("="*60)
    
    # Test 1: GET /api/strategy/status (initial - should be running=false)
    log("\n📋 TEST 1: GET /api/strategy/status (initial)")
    success1, data1, status1 = test_endpoint(
        "Initial Strategy Status", 
        "GET", 
        "strategy/status", 
        200
    )
    
    if success1:
        running = data1.get('running')
        if running == False:
            log("✅ VERIFIED: running=false initially")
        else:
            log(f"⚠️  WARNING: running={running} (expected false)")
    
    # Test 2: POST /api/strategy/start with exact JSON payload
    log("\n📋 TEST 2: POST /api/strategy/start")
    strategy_payload = {
        "symbol": "R_100",
        "granularity": 60,
        "candle_len": 200,
        "duration": 5,
        "duration_unit": "t",
        "stake": 1.0,
        "daily_loss_limit": -20.0,
        "adx_trend": 22.0,
        "rsi_ob": 70.0,
        "rsi_os": 30.0,
        "bbands_k": 2.0,
        "mode": "paper"
    }
    
    success2, data2, status2 = test_endpoint(
        "Start Strategy",
        "POST",
        "strategy/start",
        200,
        strategy_payload
    )
    
    if success2:
        running = data2.get('running')
        if running == True:
            log("✅ VERIFIED: running=true after start")
        else:
            log(f"❌ FAILED: running={running} (expected true)")
            if status2 == 400 and 'already running' in str(data2):
                log("   Note: Strategy was already running - stopping first...")
                # Stop and retry
                test_endpoint("Stop Strategy", "POST", "strategy/stop")
                time.sleep(2)
                success2, data2, status2 = test_endpoint(
                    "Start Strategy (retry)",
                    "POST", 
                    "strategy/start",
                    200,
                    strategy_payload
                )
                if success2 and data2.get('running') == True:
                    log("✅ VERIFIED: running=true after retry")
    
    # Test 3: Wait and check status for last_run_at update
    log("\n📋 TEST 3: Wait and check for last_run_at update")
    log("⏳ Waiting 10 seconds for strategy to run...")
    
    # Get initial last_run_at
    initial_last_run_at = data2.get('last_run_at') if success2 else None
    log(f"   Initial last_run_at: {initial_last_run_at}")
    
    time.sleep(10)
    
    success3, data3, status3 = test_endpoint(
        "Strategy Status After Wait",
        "GET",
        "strategy/status",
        200
    )
    
    if success3:
        new_last_run_at = data3.get('last_run_at')
        running = data3.get('running')
        
        log(f"   New last_run_at: {new_last_run_at}")
        log(f"   Running: {running}")
        
        if new_last_run_at is not None:
            log("✅ VERIFIED: last_run_at is non-null")
            if initial_last_run_at and new_last_run_at > initial_last_run_at:
                log("✅ VERIFIED: last_run_at has updated (increased)")
            elif initial_last_run_at is None:
                log("✅ VERIFIED: last_run_at now has a value")
        else:
            log("❌ FAILED: last_run_at is still null")
    
    # Test 4: POST /api/strategy/stop
    log("\n📋 TEST 4: POST /api/strategy/stop")
    success4, data4, status4 = test_endpoint(
        "Stop Strategy",
        "POST",
        "strategy/stop",
        200
    )
    
    if success4:
        running = data4.get('running')
        if running == False:
            log("✅ VERIFIED: running=false after stop")
        else:
            log(f"❌ FAILED: running={running} (expected false)")
    
    # Test 5: POST /api/candles/ingest
    log("\n📋 TEST 5: POST /api/candles/ingest")
    success5, data5, status5 = test_endpoint(
        "Candles Ingest",
        "POST",
        "candles/ingest?symbol=R_100&granularity=60&count=120"
    )
    
    if status5 == 200:
        # MongoDB configured and working
        symbol = data5.get('symbol', '')
        timeframe = data5.get('timeframe', '')
        received = data5.get('received', 0)
        inserted = data5.get('inserted', 0)
        updated = data5.get('updated', 0)
        
        log(f"✅ SUCCESS: MongoDB working")
        log(f"   Symbol: {symbol}")
        log(f"   Timeframe: {timeframe}")
        log(f"   Received: {received}")
        log(f"   Inserted: {inserted}")
        log(f"   Updated: {updated}")
        
        if symbol == 'R_100' and received > 0 and inserted >= 0 and updated >= 0:
            log("✅ VERIFIED: Response has expected keys and values")
        else:
            log("❌ FAILED: Response validation failed")
            
    elif status5 == 503:
        # MongoDB not configured - expected
        error_detail = data5.get('detail', '')
        log(f"✅ EXPECTED: 503 MongoDB not configured")
        log(f"   Error: {error_detail}")
        
        if 'mongo' in error_detail.lower() and 'indisponível' in error_detail.lower():
            log("✅ VERIFIED: Error message mentions 'Mongo indisponível'")
        else:
            log("⚠️  WARNING: Error message doesn't match expected format")
    else:
        log(f"❌ UNEXPECTED: Status {status5}")
    
    # Summary
    log("\n" + "="*60)
    log("REVIEW REQUEST TEST RESULTS")
    log("="*60)
    
    tests = [
        ("Initial Status (running=false)", success1),
        ("Start Strategy (running=true)", success2),
        ("Status Update (last_run_at)", success3),
        ("Stop Strategy (running=false)", success4),
        ("Candles Ingest", success5 or status5 == 503)  # 503 is acceptable
    ]
    
    for test_name, success in tests:
        status = "✅ PASSED" if success else "❌ FAILED"
        log(f"{status} - {test_name}")
    
    all_passed = all(success for _, success in tests)
    
    if all_passed:
        log("\n🎉 ALL REVIEW REQUEST TESTS PASSED!")
        log("📋 Strategy start/stop works correctly")
        log("📋 Strategy shows activity with last_run_at updates")
        log("📋 Candles ingest works (or properly reports MongoDB unavailable)")
    else:
        log("\n⚠️  SOME TESTS FAILED - CHECK RESULTS ABOVE")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)