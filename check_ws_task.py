#!/usr/bin/env python3
"""
Check if the WebSocket task is running in the backend
"""

import requests
import json

def check_backend_internals():
    """Check backend internal state via a debug endpoint"""
    print("🔍 Checking Backend WebSocket Task Status")
    
    # We'll need to add a debug endpoint to check the internal state
    # For now, let's check what we can from the existing endpoints
    
    try:
        response = requests.get("http://localhost:8001/api/deriv/status", timeout=10)
        if response.status_code == 200:
            data = response.json()
            print(f"📊 Deriv Status:")
            print(f"   Connected: {data.get('connected')}")
            print(f"   Authenticated: {data.get('authenticated')}")
            print(f"   Environment: {data.get('environment')}")
            print(f"   Symbols: {data.get('symbols')}")
            print(f"   Last Heartbeat: {data.get('last_heartbeat')}")
            
            # The fact that connected=True but last_heartbeat=None suggests
            # the WebSocket connection was established but the message loop
            # is not receiving messages properly
            
            if data.get('connected') and data.get('last_heartbeat') is None:
                print("\n🔍 DIAGNOSIS:")
                print("   ❌ WebSocket shows connected but no heartbeat received")
                print("   This indicates the WebSocket message loop is not working")
                print("   Possible causes:")
                print("   1. WebSocket loop task crashed or stopped")
                print("   2. Message processing loop has an exception")
                print("   3. WebSocket connection was established but then dropped")
                print("   4. Deriv is not sending heartbeat messages")
                
                return False
            elif data.get('connected') and data.get('last_heartbeat'):
                print("\n✅ WebSocket appears to be working (has heartbeat)")
                return True
            else:
                print("\n❌ WebSocket not connected")
                return False
        else:
            print(f"❌ Failed to get status: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Error checking status: {e}")
        return False

if __name__ == "__main__":
    check_backend_internals()