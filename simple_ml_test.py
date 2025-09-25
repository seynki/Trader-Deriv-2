#!/usr/bin/env python3
"""
Simple ML Test - Check connectivity and run basic ML training
"""

import requests
import json
import time
from datetime import datetime

def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

def test_connectivity():
    """Test basic connectivity to the API"""
    base_url = "https://finance-bot-timer-1.preview.emergentagent.com"
    api_url = f"{base_url}/api"
    
    log("🔍 Testing basic connectivity...")
    
    # Test 1: Root endpoint
    try:
        response = requests.get(f"{api_url}/", timeout=30)
        log(f"   Root endpoint: {response.status_code}")
        if response.status_code == 200:
            log(f"   Response: {response.json()}")
    except Exception as e:
        log(f"   Root endpoint failed: {e}")
        return False
    
    # Test 2: Deriv status
    try:
        response = requests.get(f"{api_url}/deriv/status", timeout=30)
        log(f"   Deriv status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            log(f"   Connected: {data.get('connected')}")
            log(f"   Authenticated: {data.get('authenticated')}")
    except Exception as e:
        log(f"   Deriv status failed: {e}")
        return False
    
    # Test 3: ML status
    try:
        response = requests.get(f"{api_url}/ml/status", timeout=30)
        log(f"   ML status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            log(f"   ML Status: {json.dumps(data, indent=2)}")
    except Exception as e:
        log(f"   ML status failed: {e}")
        return False
    
    return True

def test_simple_ml_training():
    """Test simple ML training with smaller parameters"""
    base_url = "https://finance-bot-timer-1.preview.emergentagent.com"
    api_url = f"{base_url}/api"
    
    log("🧠 Testing simple ML training...")
    
    # Use smaller parameters for faster testing
    params = (
        "source=deriv&symbol=R_100&timeframe=3m&count=1200"
        "&thresholds=0.003&horizons=3"
        "&model_type=rf&class_weight=balanced&calibrate=sigmoid&objective=precision"
    )
    
    url = f"{api_url}/ml/train?{params}"
    log(f"   URL: {url}")
    log("   ⏳ Starting training (timeout=120s)...")
    
    try:
        response = requests.post(url, timeout=120)
        log(f"   Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            log("   ✅ Training successful!")
            log(f"   Model ID: {data.get('model_id')}")
            log(f"   Rows: {data.get('rows')}")
            log(f"   Precision: {data.get('metrics', {}).get('precision')}")
            return True, data
        else:
            log(f"   ❌ Training failed: {response.status_code}")
            try:
                error_data = response.json()
                log(f"   Error: {error_data.get('detail', 'Unknown error')}")
            except:
                log(f"   Error text: {response.text}")
            return False, None
            
    except requests.exceptions.Timeout:
        log("   ❌ Training timeout after 120s")
        return False, None
    except Exception as e:
        log(f"   ❌ Training error: {e}")
        return False, None

def main():
    log("🚀 Simple ML Test Starting")
    
    # Test connectivity first
    if not test_connectivity():
        log("❌ Connectivity test failed")
        return 1
    
    log("✅ Connectivity test passed")
    
    # Test simple ML training
    success, data = test_simple_ml_training()
    
    if success:
        log("🎉 Simple ML training test passed!")
        return 0
    else:
        log("❌ Simple ML training test failed")
        return 1

if __name__ == "__main__":
    exit(main())