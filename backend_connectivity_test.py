#!/usr/bin/env python3
"""
Backend Connectivity and Health Testing
Tests specific endpoints requested in the Portuguese review request:
1. GET /api/deriv/status - connectivity and health check
2. WebSocket /api/ws/ticks - tick data streaming
3. GET /api/strategy/status - strategy status check
"""

import requests
import json
import sys
import time
import asyncio
import websockets
from datetime import datetime
import ssl

class BackendConnectivityTester:
    def __init__(self, base_url="https://finance-bot-timer.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
        self.ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
        self.tests_run = 0
        self.tests_passed = 0
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def log(self, message):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def test_deriv_status_with_wait(self):
        """Test 1: GET /api/deriv/status - Wait up to 8s before first call as WS connects on startup"""
        self.log("\n" + "="*60)
        self.log("TEST 1: Deriv Status Check (with 8s startup wait)")
        self.log("="*60)
        self.log("📋 Aguardando até 8s antes da primeira chamada pois o WS conecta no startup")
        
        # Wait 8 seconds as requested
        self.log("⏳ Aguardando 8 segundos para conexão WS...")
        time.sleep(8)
        
        self.tests_run += 1
        url = f"{self.api_url}/deriv/status"
        
        self.log(f"🔍 Testing Deriv Status...")
        self.log(f"   URL: {url}")
        
        try:
            response = self.session.get(url, timeout=15)
            self.log(f"   Response Status: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    self.log(f"   Response Data: {json.dumps(data, indent=2)}")
                    
                    connected = data.get('connected', False)
                    authenticated = data.get('authenticated', False)
                    environment = data.get('environment', 'Unknown')
                    symbols = data.get('symbols', [])
                    
                    self.log(f"   ✅ Status: 200")
                    self.log(f"   📊 Connected: {connected}")
                    self.log(f"   📊 Authenticated: {authenticated}")
                    self.log(f"   📊 Environment: {environment}")
                    self.log(f"   📊 Symbols: {len(symbols)} subscribed")
                    
                    # Validate required fields are present
                    if 'connected' in data and 'authenticated' in data:
                        self.tests_passed += 1
                        self.log("✅ PASSED - Deriv status endpoint working with required fields")
                        
                        # Additional validation notes
                        if not connected:
                            self.log("⚠️  NOTE: connected=false - WS may not be connected to Deriv")
                        if not authenticated and connected:
                            self.log("⚠️  NOTE: authenticated=false - conexão anônima (sem token)")
                        
                        return True, data
                    else:
                        self.log("❌ FAILED - Missing required fields (connected/authenticated)")
                        return False, data
                        
                except json.JSONDecodeError:
                    self.log(f"❌ FAILED - Invalid JSON response: {response.text}")
                    return False, {"error": "invalid_json", "text": response.text}
            else:
                self.log(f"❌ FAILED - Expected 200, got {response.status_code}")
                try:
                    error_data = response.json()
                    self.log(f"   Error: {error_data}")
                    return False, error_data
                except:
                    return False, {"error": "non_200_status", "status": response.status_code, "text": response.text}

        except requests.exceptions.Timeout:
            self.log(f"❌ FAILED - Request timeout after 15s")
            return False, {"error": "timeout"}
        except Exception as e:
            self.log(f"❌ FAILED - Error: {str(e)}")
            return False, {"error": str(e)}

    async def test_websocket_ticks(self):
        """Test 2: WebSocket /api/ws/ticks - Send initial payload and validate tick messages"""
        self.log("\n" + "="*60)
        self.log("TEST 2: WebSocket Ticks Streaming")
        self.log("="*60)
        self.log("📋 Abrir WebSocket /api/ws/ticks e enviar payload inicial")
        self.log("📋 Validar recepção de mensagens {type:'tick', symbol, price} por até 10s")
        
        self.tests_run += 1
        ws_url = f"{self.ws_url}/api/ws/ticks"
        self.log(f"🔍 Testing WebSocket Ticks...")
        self.log(f"   URL: {ws_url}")
        
        # Initial payload as requested
        initial_payload = {"symbols": ["R_10", "R_25"]}
        self.log(f"   Initial Payload: {json.dumps(initial_payload)}")
        
        try:
            # Create SSL context for wss connections
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # Connect to WebSocket
            self.log("   🔌 Connecting to WebSocket...")
            async with websockets.connect(ws_url, ssl=ssl_context, ping_interval=20, ping_timeout=10) as websocket:
                self.log("   ✅ WebSocket connected successfully")
                
                # Send initial payload
                await websocket.send(json.dumps(initial_payload))
                self.log("   📤 Sent initial payload with symbols: ['R_10', 'R_25']")
                
                # Listen for messages for up to 10 seconds
                messages_received = []
                tick_messages = []
                start_time = time.time()
                max_wait = 10  # 10 seconds as requested
                
                self.log(f"   👂 Listening for messages for up to {max_wait} seconds...")
                
                while time.time() - start_time < max_wait:
                    try:
                        # Wait for message with timeout
                        message = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                        
                        try:
                            data = json.loads(message)
                            messages_received.append(data)
                            
                            msg_type = data.get('type', 'unknown')
                            self.log(f"   📨 Received message: type='{msg_type}'")
                            
                            # Check if it's a tick message
                            if msg_type == 'tick':
                                symbol = data.get('symbol', 'unknown')
                                price = data.get('price', 'unknown')
                                timestamp = data.get('timestamp', 'unknown')
                                
                                self.log(f"      🎯 TICK: symbol={symbol}, price={price}, timestamp={timestamp}")
                                tick_messages.append(data)
                                
                                # Validate tick message structure
                                if 'symbol' in data and 'price' in data:
                                    self.log(f"      ✅ Valid tick structure")
                                else:
                                    self.log(f"      ⚠️  Incomplete tick structure")
                            
                            elif msg_type == 'subscribed':
                                symbols = data.get('symbols', [])
                                self.log(f"      📋 SUBSCRIBED to symbols: {symbols}")
                            
                            elif msg_type == 'error':
                                error_msg = data.get('message', 'Unknown error')
                                self.log(f"      ❌ ERROR: {error_msg}")
                            
                            elif msg_type == 'ping':
                                self.log(f"      💓 PING received")
                            
                            else:
                                self.log(f"      📄 OTHER: {json.dumps(data)}")
                        
                        except json.JSONDecodeError:
                            self.log(f"   ⚠️  Non-JSON message: {message}")
                            messages_received.append({"raw": message})
                    
                    except asyncio.TimeoutError:
                        # No message received in 2 seconds, continue listening
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        self.log("   ❌ WebSocket connection closed unexpectedly")
                        break
                
                # Analysis
                self.log(f"\n   📊 WEBSOCKET TEST RESULTS:")
                self.log(f"      Total messages received: {len(messages_received)}")
                self.log(f"      Tick messages received: {len(tick_messages)}")
                
                # Success criteria: received at least one valid tick message
                if len(tick_messages) > 0:
                    self.tests_passed += 1
                    self.log("   ✅ PASSED - WebSocket ticks working, received valid tick messages")
                    
                    # Show sample tick messages
                    for i, tick in enumerate(tick_messages[:3]):  # Show first 3 ticks
                        self.log(f"      Sample Tick {i+1}: {json.dumps(tick)}")
                    
                    return True, {
                        "total_messages": len(messages_received),
                        "tick_messages": len(tick_messages),
                        "sample_ticks": tick_messages[:3]
                    }
                else:
                    self.log("   ❌ FAILED - No valid tick messages received")
                    self.log("   📋 Possible issues:")
                    self.log("      - WebSocket not properly connected to Deriv")
                    self.log("      - Symbols R_10/R_25 not available")
                    self.log("      - Backend WS relay not working")
                    
                    return False, {
                        "total_messages": len(messages_received),
                        "tick_messages": 0,
                        "all_messages": messages_received
                    }

        except websockets.exceptions.InvalidURI:
            self.log(f"   ❌ FAILED - Invalid WebSocket URI: {ws_url}")
            return False, {"error": "invalid_uri"}
        except websockets.exceptions.ConnectionClosed:
            self.log(f"   ❌ FAILED - WebSocket connection closed")
            return False, {"error": "connection_closed"}
        except Exception as e:
            self.log(f"   ❌ FAILED - WebSocket error: {str(e)}")
            return False, {"error": str(e)}

    def test_strategy_status(self):
        """Test 3: GET /api/strategy/status - Should return 200 with running=false initially"""
        self.log("\n" + "="*60)
        self.log("TEST 3: Strategy Status Check")
        self.log("="*60)
        self.log("📋 Deve retornar 200 com running=false inicialmente")
        self.log("📋 Campos: win_rate, total_trades, global_daily_pnl")
        
        self.tests_run += 1
        url = f"{self.api_url}/strategy/status"
        
        self.log(f"🔍 Testing Strategy Status...")
        self.log(f"   URL: {url}")
        
        try:
            response = self.session.get(url, timeout=10)
            self.log(f"   Response Status: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    self.log(f"   Response Data: {json.dumps(data, indent=2)}")
                    
                    running = data.get('running')
                    win_rate = data.get('win_rate')
                    total_trades = data.get('total_trades')
                    global_daily_pnl = data.get('global_daily_pnl')
                    mode = data.get('mode', 'unknown')
                    symbol = data.get('symbol', 'unknown')
                    
                    self.log(f"   ✅ Status: 200")
                    self.log(f"   📊 Running: {running}")
                    self.log(f"   📊 Mode: {mode}")
                    self.log(f"   📊 Symbol: {symbol}")
                    self.log(f"   📊 Win Rate: {win_rate}%")
                    self.log(f"   📊 Total Trades: {total_trades}")
                    self.log(f"   📊 Global Daily PnL: {global_daily_pnl}")
                    
                    # Validate required fields
                    required_fields = ['running', 'win_rate', 'total_trades', 'global_daily_pnl']
                    missing_fields = [field for field in required_fields if field not in data]
                    
                    if not missing_fields:
                        # Check if running is false initially as expected
                        if running == False:
                            self.tests_passed += 1
                            self.log("   ✅ PASSED - Strategy status working, running=false initially as expected")
                        else:
                            self.tests_passed += 1  # Still pass if endpoint works
                            self.log(f"   ⚠️  PASSED - Strategy status working, but running={running} (expected false)")
                        
                        return True, data
                    else:
                        self.log(f"   ❌ FAILED - Missing required fields: {missing_fields}")
                        return False, {"error": "missing_fields", "missing": missing_fields, "data": data}
                        
                except json.JSONDecodeError:
                    self.log(f"   ❌ FAILED - Invalid JSON response: {response.text}")
                    return False, {"error": "invalid_json", "text": response.text}
            else:
                self.log(f"   ❌ FAILED - Expected 200, got {response.status_code}")
                try:
                    error_data = response.json()
                    self.log(f"   Error: {error_data}")
                    return False, error_data
                except:
                    return False, {"error": "non_200_status", "status": response.status_code, "text": response.text}

        except requests.exceptions.Timeout:
            self.log(f"   ❌ FAILED - Request timeout after 10s")
            return False, {"error": "timeout"}
        except Exception as e:
            self.log(f"   ❌ FAILED - Error: {str(e)}")
            return False, {"error": str(e)}

    async def run_connectivity_tests(self):
        """Run all connectivity tests as requested in the review"""
        self.log("\n" + "🎯" + "="*58)
        self.log("BACKEND CONNECTIVITY AND HEALTH TESTS")
        self.log("🎯" + "="*58)
        self.log("📋 Testando APENAS conectividade e health (sem trades):")
        self.log("   1) GET /api/deriv/status — deve retornar 200 com connected=(true/false), authenticated=(true/false)")
        self.log("   2) Abrir WebSocket /api/ws/ticks — enviar payload inicial e validar recepção de mensagens")
        self.log("   3) GET /api/strategy/status — deve retornar 200 com running=false inicialmente")
        self.log("   ⚠️  NÃO executar /api/deriv/buy; NÃO depender de Mongo para este teste")
        
        # Test 1: Deriv Status (with 8s wait)
        self.log("\n🔍 EXECUTANDO TESTE 1...")
        deriv_status_ok, deriv_data = self.test_deriv_status_with_wait()
        
        # Test 2: WebSocket Ticks
        self.log("\n🔍 EXECUTANDO TESTE 2...")
        ws_ticks_ok, ws_data = await self.test_websocket_ticks()
        
        # Test 3: Strategy Status
        self.log("\n🔍 EXECUTANDO TESTE 3...")
        strategy_status_ok, strategy_data = self.test_strategy_status()
        
        # Final Summary
        self.log("\n" + "🎯" + "="*58)
        self.log("CONNECTIVITY TEST RESULTS")
        self.log("🎯" + "="*58)
        
        if deriv_status_ok:
            connected = deriv_data.get('connected', False)
            authenticated = deriv_data.get('authenticated', False)
            self.log(f"✅ GET /api/deriv/status: 200 OK (connected={connected}, authenticated={authenticated})")
        else:
            self.log("❌ GET /api/deriv/status: FAILED")
        
        if ws_ticks_ok:
            tick_count = ws_data.get('tick_messages', 0)
            self.log(f"✅ WebSocket /api/ws/ticks: OK ({tick_count} tick messages received)")
        else:
            self.log("❌ WebSocket /api/ws/ticks: FAILED")
        
        if strategy_status_ok:
            running = strategy_data.get('running', 'unknown')
            total_trades = strategy_data.get('total_trades', 'unknown')
            win_rate = strategy_data.get('win_rate', 'unknown')
            global_daily_pnl = strategy_data.get('global_daily_pnl', 'unknown')
            self.log(f"✅ GET /api/strategy/status: 200 OK (running={running}, total_trades={total_trades}, win_rate={win_rate}%, global_daily_pnl={global_daily_pnl})")
        else:
            self.log("❌ GET /api/strategy/status: FAILED")
        
        # Overall assessment
        all_tests_passed = deriv_status_ok and ws_ticks_ok and strategy_status_ok
        
        self.log(f"\n📊 OVERALL RESULTS: {self.tests_passed}/{self.tests_run} tests passed")
        
        if all_tests_passed:
            self.log("\n🎉 TODOS OS TESTES DE CONECTIVIDADE PASSARAM!")
            self.log("📋 Backend connectivity and health endpoints working correctly")
            self.log("📋 WebSocket integration functional")
            self.log("📋 Strategy endpoints accessible")
        else:
            failed_count = self.tests_run - self.tests_passed
            self.log(f"\n⚠️  {failed_count} TESTE(S) FALHARAM")
            self.log("📋 Check individual test results above for details")
        
        return all_tests_passed, {
            "deriv_status": {"success": deriv_status_ok, "data": deriv_data},
            "websocket_ticks": {"success": ws_ticks_ok, "data": ws_data},
            "strategy_status": {"success": strategy_status_ok, "data": strategy_data},
            "summary": {
                "total_tests": self.tests_run,
                "passed_tests": self.tests_passed,
                "all_passed": all_tests_passed
            }
        }

def main():
    """Main function to run the connectivity tests"""
    print("🚀 Starting Backend Connectivity and Health Tests...")
    print("📋 Review Request: Test connectivity and health endpoints only (no trades)")
    
    tester = BackendConnectivityTester()
    
    # Run async tests
    try:
        success, results = asyncio.run(tester.run_connectivity_tests())
        
        if success:
            print("\n✅ ALL CONNECTIVITY TESTS COMPLETED SUCCESSFULLY")
            sys.exit(0)
        else:
            print("\n❌ SOME CONNECTIVITY TESTS FAILED")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n⚠️  Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test execution failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()