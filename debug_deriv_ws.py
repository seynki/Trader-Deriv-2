#!/usr/bin/env python3
"""
Debug script to check Deriv WebSocket connection and contract subscription
"""

import requests
import json
import time
import asyncio
import websockets

async def test_deriv_ws_direct():
    """Test direct connection to Deriv WebSocket"""
    print("🔍 Testing direct Deriv WebSocket connection...")
    
    # Use the same credentials as the backend
    app_id = "96285"
    token = "uXQSmwOsSXIMGhH"
    ws_url = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
    
    try:
        print(f"   Connecting to: {ws_url}")
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
            print("   ✅ Connected to Deriv WebSocket")
            
            # Send authorize
            auth_msg = {"authorize": token}
            await ws.send(json.dumps(auth_msg))
            print("   📤 Sent authorize message")
            
            # Wait for authorize response
            response = await asyncio.wait_for(ws.recv(), timeout=10)
            auth_data = json.loads(response)
            print(f"   📥 Authorize response: {auth_data}")
            
            if auth_data.get("msg_type") == "authorize" and not auth_data.get("error"):
                print("   ✅ Successfully authenticated")
                
                # Test contract subscription (use a recent contract ID)
                contract_id = 292129182208  # From our previous test
                contract_msg = {
                    "proposal_open_contract": 1,
                    "contract_id": contract_id,
                    "subscribe": 1,
                }
                await ws.send(json.dumps(contract_msg))
                print(f"   📤 Sent contract subscription for {contract_id}")
                
                # Listen for messages for 30 seconds
                print("   ⏳ Listening for messages for 30 seconds...")
                start_time = time.time()
                
                while time.time() - start_time < 30:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=5)
                        data = json.loads(message)
                        msg_type = data.get("msg_type")
                        
                        if msg_type == "proposal_open_contract":
                            poc = data.get("proposal_open_contract", {})
                            print(f"   📥 Contract update: ID={poc.get('contract_id')}, "
                                  f"status={poc.get('status')}, "
                                  f"is_expired={poc.get('is_expired')}, "
                                  f"profit={poc.get('profit')}")
                        elif msg_type == "heartbeat":
                            print("   💓 Heartbeat received")
                        elif msg_type == "error":
                            print(f"   ❌ Error: {data}")
                        else:
                            print(f"   📥 Other message: {msg_type}")
                            
                    except asyncio.TimeoutError:
                        print("   ⏰ No message received in 5s")
                        continue
                
                print("   ✅ WebSocket test completed")
                return True
            else:
                print(f"   ❌ Authentication failed: {auth_data}")
                return False
                
    except Exception as e:
        print(f"   ❌ WebSocket connection failed: {e}")
        return False

def test_backend_status():
    """Test backend Deriv status"""
    print("\n🔍 Testing backend Deriv status...")
    
    try:
        response = requests.get("http://localhost:8001/api/deriv/status", timeout=10)
        if response.status_code == 200:
            data = response.json()
            print(f"   Backend status: {data}")
            return data.get("connected") and data.get("authenticated")
        else:
            print(f"   ❌ Backend status failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"   ❌ Backend status error: {e}")
        return False

async def main():
    print("🚀 Deriv WebSocket Debug Test")
    
    # Test backend status first
    backend_ok = test_backend_status()
    
    # Test direct WebSocket connection
    ws_ok = await test_deriv_ws_direct()
    
    print(f"\n📊 Results:")
    print(f"   Backend Deriv Status: {'✅' if backend_ok else '❌'}")
    print(f"   Direct WebSocket Test: {'✅' if ws_ok else '❌'}")
    
    if backend_ok and not ws_ok:
        print("\n🔍 Backend shows connected but direct test failed - possible backend issue")
    elif not backend_ok and ws_ok:
        print("\n🔍 Direct connection works but backend doesn't - backend configuration issue")
    elif not backend_ok and not ws_ok:
        print("\n🔍 Both failed - likely Deriv API or credentials issue")
    else:
        print("\n🔍 Both working - issue might be with contract tracking logic")

if __name__ == "__main__":
    asyncio.run(main())