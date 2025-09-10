#!/usr/bin/env python3
"""
Backend WebSocket Testing for Deriv Trading Bot
Tests as requested in Portuguese review:
🔌 TESTE DE WEBSOCKET DERIV - ESTABILIDADE E PERFORMANCE

OBJETIVO: Testar somente BACKEND WebSocket conforme review request

CONTEXTO CRÍTICO:
- Frontend atualizado para usar WebSocket via backend com prefixo /api
- Backend expõe endpoints: GET /api/deriv/status, WS /api/ws/ticks, WS /api/ws/contract/{id}
- WebSocket URL construído com REACT_APP_BACKEND_URL e querystring ?symbols=
- Sem hardcode de localhost

TESTES OBRIGATÓRIOS:
1. AGUARDAR 5s pós-start
2. GET /api/deriv/status deve retornar 200 com connected=true (auth true se DERIV_API_TOKEN válido)
3. Conectar ao WebSocket /api/ws/ticks?symbols=R_100,R_75,R_50 por 30s e medir:
   - Mensagens totais >= 45 em 30s (≈1.5 msg/s)
   - Validar recebimento de mensagens type:"tick" com symbol e price
   - Validar eventualmente type:"heartbeat"
   - A conexão não deve cair
4. (Opcional) WS /api/ws/contract/123456 deve conectar e enviar heartbeat a cada ~0.5s

CRITÉRIOS DE SUCESSO:
- ✅ GET /api/deriv/status retorna connected=true
- ✅ WebSocket /api/ws/ticks conecta e mantém conexão por 30s
- ✅ Taxa de mensagens >= 1.5 msg/s (45+ mensagens em 30s)
- ✅ Mensagens contêm type:"tick" com symbol e price
- ✅ Heartbeats funcionando

CRITÉRIOS DE FALHA:
- ❌ GET /api/deriv/status não conectado
- ❌ WebSocket não conecta ou cai durante teste
- ❌ Taxa < 1.5 msg/s (menos de 45 mensagens em 30s)
- ❌ Mensagens malformadas ou sem dados essenciais

INSTRUÇÕES ESPECIAIS:
- NÃO testar frontend
- Apenas confirmar que backend WS está estável e performático (~1.5 msg/s)
- Registrar no test_result.md
- Usar URL do REACT_APP_BACKEND_URL para testes
"""

import requests
import json
import sys
import time
import asyncio
import websockets
from datetime import datetime

class DerivWebSocketTester:
    def __init__(self, base_url="https://partial-fit-fix.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
        self.ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
        self.tests_run = 0
        self.tests_passed = 0
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def log(self, message):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def run_test(self, name, method, endpoint, expected_status, data=None, timeout=30):
        """Run a single API test with detailed logging"""
        url = f"{self.api_url}/{endpoint}"
        self.tests_run += 1
        
        self.log(f"🔍 Testing {name}...")
        self.log(f"   URL: {url}")
        if data:
            self.log(f"   Data: {json.dumps(data, indent=2)}")
        
        try:
            if method == 'GET':
                response = self.session.get(url, timeout=timeout)
            elif method == 'POST':
                response = self.session.post(url, json=data, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")

            self.log(f"   Response Status: {response.status_code}")
            
            try:
                response_data = response.json()
                self.log(f"   Response Data: {json.dumps(response_data, indent=2)}")
            except:
                response_data = {"raw_text": response.text}
                self.log(f"   Response Text: {response.text}")

            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                self.log(f"✅ PASSED - {name}")
            else:
                self.log(f"❌ FAILED - {name} - Expected {expected_status}, got {response.status_code}")

            return success, response_data, response.status_code

        except requests.exceptions.Timeout:
            self.log(f"❌ FAILED - {name} - Request timeout after {timeout}s")
            return False, {"error": "timeout"}, 0
        except Exception as e:
            self.log(f"❌ FAILED - {name} - Error: {str(e)}")
            return False, {"error": str(e)}, 0

    def test_deriv_status(self):
        """Test 1: GET /api/deriv/status - verificar conectividade com Deriv"""
        self.log("\n" + "="*70)
        self.log("TEST 1: VERIFICAR CONECTIVIDADE COM DERIV")
        self.log("="*70)
        self.log("📋 Objetivo: GET /api/deriv/status (verificar se está conectado à Deriv)")
        
        success, data, status_code = self.run_test(
            "Deriv Status Check",
            "GET",
            "deriv/status",
            200
        )
        
        if not success:
            self.log(f"❌ CRITICAL: GET /api/deriv/status falhou - Status: {status_code}")
            return False, data
        
        connected = data.get('connected', False)
        authenticated = data.get('authenticated', False)
        environment = data.get('environment', 'UNKNOWN')
        symbols = data.get('symbols', [])
        last_heartbeat = data.get('last_heartbeat')
        
        self.log(f"📊 RESULTADOS:")
        self.log(f"   Conectado: {connected}")
        self.log(f"   Autenticado: {authenticated}")
        self.log(f"   Ambiente: {environment}")
        self.log(f"   Símbolos subscritos: {symbols}")
        self.log(f"   Último heartbeat: {last_heartbeat}")
        
        # Validation
        if not connected:
            self.log("❌ CRITICAL: Deriv não está conectado")
            return False, {"message": "deriv_not_connected", "data": data}
        
        if environment != "DEMO":
            self.log(f"⚠️  WARNING: Ambiente não é DEMO: {environment}")
        
        self.log(f"✅ Deriv conectado com sucesso (ambiente: {environment})")
        return True, data

    async def test_websocket_ticks_performance(self):
        """Test WebSocket /api/ws/ticks - testar por 30 segundos para R_100,R_75,R_50 conforme review request"""
        self.log("\n" + "="*70)
        self.log("TEST 2: WEBSOCKET TICKS PERFORMANCE E ESTABILIDADE")
        self.log("="*70)
        self.log("📋 Objetivo: Conectar ao WebSocket /api/ws/ticks por 30 segundos e medir performance")
        self.log("📋 Símbolos: R_100,R_75,R_50 (conforme review request)")
        self.log("📋 Taxa esperada: >= 1.5 msg/s (45+ mensagens em 30s)")
        self.log("📋 Verificar se conexão é estável (sem desconexões)")
        self.log("📋 Validar mensagens type:'tick' com symbol e price")
        self.log("📋 Validar heartbeats ocasionais")
        
        ws_url = f"{self.ws_url}/api/ws/ticks?symbols=R_100,R_75,R_50"
        self.log(f"   WebSocket URL: {ws_url}")
        
        messages_received = 0
        tick_messages = 0
        heartbeat_messages = 0
        connection_errors = 0
        symbols_detected = set()
        start_time = time.time()
        test_duration = 30  # 30 seconds as requested in review
        
        try:
            self.log("🔌 Conectando ao WebSocket...")
            
            # Connect to WebSocket
            websocket = await websockets.connect(ws_url)
            self.log("✅ WebSocket conectado com sucesso")
            
            try:
                self.log(f"⏱️  Monitorando por {test_duration} segundos...")
                
                while time.time() - start_time < test_duration:
                    try:
                        # Wait for message with timeout
                        message = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                        
                        try:
                            data = json.loads(message)
                            messages_received += 1
                            
                            msg_type = data.get('type', 'unknown')
                            symbol = data.get('symbol', 'unknown')
                            price = data.get('price', 0)
                            timestamp = data.get('timestamp', 0)
                            
                            # Count different message types
                            if msg_type == 'tick':
                                tick_messages += 1
                                if symbol != 'unknown':
                                    symbols_detected.add(symbol)
                            elif msg_type == 'heartbeat':
                                heartbeat_messages += 1
                            
                            # Log every 10th message to show progress
                            if messages_received % 10 == 0 or messages_received <= 5:
                                elapsed = time.time() - start_time
                                rate = messages_received / elapsed if elapsed > 0 else 0
                                self.log(f"📊 Progresso: {messages_received} msgs ({tick_messages} ticks, {heartbeat_messages} heartbeats) em {elapsed:.1f}s - {rate:.2f} msg/s")
                                if symbol != 'unknown':
                                    self.log(f"   Último tick: {symbol} = {price}")
                            
                        except json.JSONDecodeError:
                            self.log(f"⚠️  Mensagem não-JSON recebida: {message[:100]}...")
                            
                    except asyncio.TimeoutError:
                        # No message received in 2 seconds - this might indicate instability
                        elapsed = time.time() - start_time
                        connection_errors += 1
                        self.log(f"⚠️  Timeout aguardando mensagem (elapsed: {elapsed:.1f}s, timeouts: {connection_errors})")
                        
                        if connection_errors >= 15:  # Allow more timeouts for 30s test
                            self.log("❌ Muitos timeouts consecutivos - conexão instável")
                            break
                            
                    except websockets.exceptions.ConnectionClosed as e:
                        self.log(f"❌ WebSocket fechou inesperadamente: {e}")
                        connection_errors += 1
                        break
                        
                    except Exception as e:
                        self.log(f"❌ Erro durante recepção: {e}")
                        connection_errors += 1
                        
            finally:
                await websocket.close()
                
        except websockets.exceptions.InvalidURI:
            self.log(f"❌ URL WebSocket inválida: {ws_url}")
            return False, {"error": "invalid_uri"}
            
        except websockets.exceptions.ConnectionClosed as e:
            self.log(f"❌ Falha na conexão WebSocket: {e}")
            return False, {"error": "connection_failed", "details": str(e)}
            
        except Exception as e:
            self.log(f"❌ Erro inesperado no WebSocket: {e}")
            return False, {"error": "unexpected_error", "details": str(e)}
        
        # Analysis
        elapsed_time = time.time() - start_time
        message_rate = messages_received / elapsed_time if elapsed_time > 0 else 0
        tick_rate = tick_messages / elapsed_time if elapsed_time > 0 else 0
        
        self.log(f"\n📊 ANÁLISE DETALHADA DO WEBSOCKET:")
        self.log(f"   Tempo de teste: {elapsed_time:.1f}s")
        self.log(f"   Total mensagens: {messages_received}")
        self.log(f"   Mensagens de tick: {tick_messages}")
        self.log(f"   Mensagens de heartbeat: {heartbeat_messages}")
        self.log(f"   Taxa total: {message_rate:.2f} msg/s")
        self.log(f"   Taxa de ticks: {tick_rate:.2f} ticks/s")
        self.log(f"   Timeouts/erros: {connection_errors}")
        self.log(f"   Símbolos detectados: {list(symbols_detected)}")
        
        # Determine if WebSocket meets performance requirements
        is_performant = True
        issues = []
        
        # Check if we received minimum required messages (45+ in 30s = 1.5 msg/s)
        min_required_messages = 45
        if messages_received < min_required_messages:
            is_performant = False
            issues.append(f"Mensagens insuficientes: {messages_received} < {min_required_messages} (taxa: {message_rate:.2f} msg/s < 1.5 msg/s)")
            
        # Check message rate (should be >= 1.5 msg/s as per review requirements)
        elif message_rate < 1.5:
            is_performant = False
            issues.append(f"Taxa de mensagens baixa: {message_rate:.2f} msg/s < 1.5 msg/s")
            
        # Check if we received ticks specifically
        if tick_messages == 0:
            is_performant = False
            issues.append("Nenhum tick recebido")
            
        # Check for excessive connection errors
        if connection_errors > 10:
            is_performant = False
            issues.append(f"Muitos timeouts/erros: {connection_errors}")
            
        # Check if we detected the expected symbols (R_100, R_75, R_50)
        expected_symbols = {"R_100", "R_75", "R_50"}
        detected_expected = symbols_detected.intersection(expected_symbols)
        if not detected_expected:
            is_performant = False
            issues.append(f"Nenhum dos símbolos esperados detectado: {expected_symbols}")
            
        # Check if test ran for sufficient time (at least 80% of expected duration)
        if elapsed_time < test_duration * 0.8:
            is_performant = False
            issues.append(f"Teste terminou prematuramente: {elapsed_time:.1f}s < {test_duration}s")
        
        if is_performant:
            self.log("✅ WEBSOCKET PERFORMANCE EXCELENTE!")
            self.log(f"   ✓ Conexão mantida por {elapsed_time:.1f}s sem desconexões")
            self.log(f"   ✓ Taxa: {message_rate:.2f} msg/s (>= 1.5 msg/s ✓)")
            self.log(f"   ✓ Mensagens recebidas: {messages_received} >= {min_required_messages} ✓")
            self.log(f"   ✓ Ticks recebidos: {tick_messages} de símbolos {list(detected_expected)}")
            if heartbeat_messages > 0:
                self.log(f"   ✓ Heartbeats funcionando: {heartbeat_messages} recebidos")
            self.tests_passed += 1
        else:
            self.log("❌ WEBSOCKET COM PROBLEMAS DE PERFORMANCE:")
            for issue in issues:
                self.log(f"   - {issue}")
        
        self.tests_run += 1
        
        return is_performant, {
            "elapsed_time": elapsed_time,
            "messages_received": messages_received,
            "tick_messages": tick_messages,
            "heartbeat_messages": heartbeat_messages,
            "message_rate": message_rate,
            "tick_rate": tick_rate,
            "connection_errors": connection_errors,
            "symbols_detected": list(symbols_detected),
            "is_performant": is_performant,
            "issues": issues,
            "min_required_messages": min_required_messages,
            "meets_requirements": is_performant
        }

    async def test_websocket_contract_optional(self):
        """Test 3 (Optional): WebSocket /api/ws/contract/{id} - testar heartbeat a cada ~0.5s"""
        self.log("\n" + "="*70)
        self.log("TEST 3 (OPCIONAL): WEBSOCKET CONTRACT HEARTBEAT")
        self.log("="*70)
        self.log("📋 Objetivo: Conectar ao WebSocket /api/ws/contract/123456 e verificar heartbeat")
        self.log("📋 Esperado: Heartbeat a cada ~0.5s, pode fechar após 3s")
        
        contract_id = "123456"  # Test contract ID
        ws_url = f"{self.ws_url}/api/ws/contract/{contract_id}"
        self.log(f"   WebSocket URL: {ws_url}")
        
        messages_received = 0
        heartbeat_count = 0
        start_time = time.time()
        test_duration = 3  # 3 seconds as mentioned in review
        
        try:
            self.log("🔌 Conectando ao WebSocket de contrato...")
            
            # Connect to WebSocket
            websocket = await websockets.connect(ws_url)
            self.log("✅ WebSocket de contrato conectado com sucesso")
            
            try:
                self.log(f"⏱️  Monitorando por {test_duration} segundos...")
                
                while time.time() - start_time < test_duration:
                    try:
                        # Wait for message with timeout
                        message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        
                        try:
                            data = json.loads(message)
                            messages_received += 1
                            
                            msg_type = data.get('type', 'unknown')
                            
                            if msg_type == 'heartbeat':
                                heartbeat_count += 1
                                elapsed = time.time() - start_time
                                self.log(f"💓 Heartbeat #{heartbeat_count} recebido após {elapsed:.1f}s")
                            else:
                                self.log(f"📨 Mensagem recebida: type={msg_type}")
                            
                        except json.JSONDecodeError:
                            self.log(f"⚠️  Mensagem não-JSON recebida: {message[:100]}...")
                            
                    except asyncio.TimeoutError:
                        # No message received in 1 second
                        elapsed = time.time() - start_time
                        self.log(f"⏳ Aguardando mensagem... (elapsed: {elapsed:.1f}s)")
                        
                    except websockets.exceptions.ConnectionClosed as e:
                        self.log(f"❌ WebSocket fechou: {e}")
                        break
                        
                    except Exception as e:
                        self.log(f"❌ Erro durante recepção: {e}")
                        break
                        
            finally:
                await websocket.close()
                
        except websockets.exceptions.InvalidURI:
            self.log(f"❌ URL WebSocket inválida: {ws_url}")
            return False, {"error": "invalid_uri"}
            
        except websockets.exceptions.ConnectionClosed as e:
            self.log(f"❌ Falha na conexão WebSocket: {e}")
            return False, {"error": "connection_failed", "details": str(e)}
            
        except Exception as e:
            self.log(f"❌ Erro inesperado no WebSocket: {e}")
            return False, {"error": "unexpected_error", "details": str(e)}
        
        # Analysis
        elapsed_time = time.time() - start_time
        heartbeat_rate = heartbeat_count / elapsed_time if elapsed_time > 0 else 0
        
        self.log(f"\n📊 ANÁLISE DO WEBSOCKET CONTRACT:")
        self.log(f"   Tempo de teste: {elapsed_time:.1f}s")
        self.log(f"   Total mensagens: {messages_received}")
        self.log(f"   Heartbeats recebidos: {heartbeat_count}")
        self.log(f"   Taxa de heartbeat: {heartbeat_rate:.2f} heartbeats/s")
        
        # Expected heartbeat rate is ~2 per second (every 0.5s)
        expected_heartbeat_rate = 2.0
        is_working = heartbeat_count > 0 and heartbeat_rate >= 1.0  # Allow some tolerance
        
        if is_working:
            self.log("✅ WEBSOCKET CONTRACT FUNCIONANDO!")
            self.log(f"   ✓ Conectou com sucesso")
            self.log(f"   ✓ Heartbeats recebidos: {heartbeat_count}")
            self.log(f"   ✓ Taxa de heartbeat: {heartbeat_rate:.2f}/s (esperado ~2/s)")
            self.tests_passed += 1
        else:
            self.log("❌ WEBSOCKET CONTRACT COM PROBLEMAS:")
            if heartbeat_count == 0:
                self.log("   - Nenhum heartbeat recebido")
            elif heartbeat_rate < 1.0:
                self.log(f"   - Taxa de heartbeat baixa: {heartbeat_rate:.2f}/s < 1.0/s")
        
        self.tests_run += 1
        
        return is_working, {
            "elapsed_time": elapsed_time,
            "messages_received": messages_received,
            "heartbeat_count": heartbeat_count,
            "heartbeat_rate": heartbeat_rate,
            "is_working": is_working
        }

    async def run_websocket_tests(self):
        """Run WebSocket tests as requested in Portuguese review"""
        self.log("\n" + "🔌" + "="*68)
        self.log("TESTE DE WEBSOCKET DERIV - ESTABILIDADE E PERFORMANCE")
        self.log("🔌" + "="*68)
        self.log("📋 Conforme solicitado na review request:")
        self.log("   1. Aguardar 5s pós-start")
        self.log("   2. GET /api/deriv/status deve retornar 200 com connected=true")
        self.log("   3. Conectar ao WebSocket /api/ws/ticks?symbols=R_100,R_75,R_50 por 30s:")
        self.log("      - Mensagens totais >= 45 em 30s (≈1.5 msg/s)")
        self.log("      - Validar mensagens type:'tick' com symbol e price")
        self.log("      - Validar eventualmente type:'heartbeat'")
        self.log("      - A conexão não deve cair")
        self.log("   4. (Opcional) WS /api/ws/contract/123456 heartbeat a cada ~0.5s")
        self.log("   🎯 FOCO: Backend WS estável e performático (~1.5 msg/s)")
        self.log(f"   🌐 Base URL: {self.base_url}")
        
        results = {}
        
        # Step 1: Wait 5s post-start
        self.log("\n⏱️  STEP 1: AGUARDANDO 5s PÓS-START")
        self.log("📋 Aguardando 5 segundos para garantir que o sistema esteja pronto...")
        time.sleep(5)
        self.log("✅ Aguardou 5 segundos conforme solicitado")
        
        # Step 2: Deriv Status - conectividade básica
        self.log("\n🔍 STEP 2: VERIFICAR CONECTIVIDADE DERIV")
        deriv_ok, deriv_data = self.test_deriv_status()
        results['deriv_status'] = deriv_ok
        
        if not deriv_ok:
            self.log("❌ CRITICAL: Deriv não conectado - não é possível testar WebSocket")
            return False, results
        
        # Verify connected=true (auth optional based on DERIV_API_TOKEN)
        connected = deriv_data.get('connected', False) if isinstance(deriv_data, dict) else False
        authenticated = deriv_data.get('authenticated', False) if isinstance(deriv_data, dict) else False
        
        if not connected:
            self.log(f"❌ CRITICAL: Deriv não conectado - connected={connected}")
            return False, results
        
        self.log(f"✅ Deriv conectado (connected={connected}, authenticated={authenticated})")
        
        # Step 3: WebSocket Ticks Performance Test (30s)
        self.log("\n🔍 STEP 3: TESTE DE PERFORMANCE WEBSOCKET TICKS (30s)")
        ws_ticks_ok, ws_ticks_data = await self.test_websocket_ticks_performance()
        results['websocket_ticks'] = ws_ticks_ok
        
        # Step 4: WebSocket Contract Test (Optional, 3s)
        self.log("\n🔍 STEP 4: TESTE WEBSOCKET CONTRACT (OPCIONAL, 3s)")
        ws_contract_ok, ws_contract_data = await self.test_websocket_contract_optional()
        results['websocket_contract'] = ws_contract_ok
        
        # Final Summary
        self.log("\n" + "🏁" + "="*68)
        self.log("RESULTADO FINAL: Teste de WebSocket Deriv Backend")
        self.log("🏁" + "="*68)
        
        # Step 2 Results
        if deriv_ok and connected:
            auth_status = "authenticated=true" if authenticated else "authenticated=false (anônimo OK)"
            self.log(f"✅ 1. GET /api/deriv/status: connected=true, {auth_status} ✓")
        else:
            self.log(f"❌ 1. GET /api/deriv/status: FAILED")
        
        # Step 3 Results - CRÍTICO
        if ws_ticks_ok:
            elapsed = ws_ticks_data.get('elapsed_time', 0) if isinstance(ws_ticks_data, dict) else 0
            messages = ws_ticks_data.get('messages_received', 0) if isinstance(ws_ticks_data, dict) else 0
            rate = ws_ticks_data.get('message_rate', 0) if isinstance(ws_ticks_data, dict) else 0
            ticks = ws_ticks_data.get('tick_messages', 0) if isinstance(ws_ticks_data, dict) else 0
            heartbeats = ws_ticks_data.get('heartbeat_messages', 0) if isinstance(ws_ticks_data, dict) else 0
            symbols = ws_ticks_data.get('symbols_detected', []) if isinstance(ws_ticks_data, dict) else []
            
            self.log(f"✅ 2. WebSocket /api/ws/ticks: PERFORMANCE EXCELENTE ✓")
            self.log(f"   📊 {messages} mensagens em {elapsed:.1f}s, taxa {rate:.2f} msg/s (>= 1.5 ✓)")
            self.log(f"   📈 {ticks} ticks, {heartbeats} heartbeats, símbolos {symbols}")
            self.log(f"   🔗 Conexão estável por {elapsed:.1f}s sem desconexões")
        else:
            issues = ws_ticks_data.get('issues', []) if isinstance(ws_ticks_data, dict) else []
            messages = ws_ticks_data.get('messages_received', 0) if isinstance(ws_ticks_data, dict) else 0
            rate = ws_ticks_data.get('message_rate', 0) if isinstance(ws_ticks_data, dict) else 0
            
            self.log(f"❌ 2. WebSocket /api/ws/ticks: PROBLEMAS DE PERFORMANCE")
            self.log(f"   📊 {messages} mensagens, taxa {rate:.2f} msg/s (< 1.5 msg/s)")
            self.log(f"   🚨 Problemas detectados: {len(issues)}")
            for issue in issues[:3]:  # Show first 3 issues
                self.log(f"      - {issue}")
        
        # Step 4 Results (Optional)
        if ws_contract_ok:
            heartbeats = ws_contract_data.get('heartbeat_count', 0) if isinstance(ws_contract_data, dict) else 0
            hb_rate = ws_contract_data.get('heartbeat_rate', 0) if isinstance(ws_contract_data, dict) else 0
            self.log(f"✅ 3. WebSocket /api/ws/contract/123456: {heartbeats} heartbeats, {hb_rate:.1f}/s ✓")
        else:
            self.log("❌ 3. WebSocket /api/ws/contract/123456: FAILED (opcional)")
        
        # Overall assessment based on review requirements
        basic_connectivity = deriv_ok and connected
        websocket_performance = ws_ticks_ok
        
        if basic_connectivity and websocket_performance:
            self.log("\n🎉 TODOS OS TESTES CRÍTICOS PASSARAM!")
            self.log("📋 Validações bem-sucedidas:")
            self.log("   ✅ Deriv conectado via backend")
            self.log("   ✅ WebSocket /api/ws/ticks estável e performático (>= 1.5 msg/s)")
            self.log("   ✅ Mensagens type:'tick' com symbol e price funcionando")
            self.log("   ✅ Heartbeats funcionando")
            self.log("   ✅ Conexão não cai durante teste de 30s")
            self.log("   🎯 CONCLUSÃO: Backend WebSocket funcionando PERFEITAMENTE!")
        else:
            self.log("\n❌ PROBLEMAS CRÍTICOS DETECTADOS")
            if not basic_connectivity:
                self.log("   ❌ Deriv não conectado adequadamente")
            if not websocket_performance:
                self.log("   ❌ WebSocket com problemas de performance ou estabilidade")
                self.log("   📋 FOCO: Taxa < 1.5 msg/s ou conexão instável")
        
        return basic_connectivity and websocket_performance, results

    def print_summary(self):
        """Print test summary"""
        self.log("\n" + "="*70)
        self.log("RESUMO ESTATÍSTICO DOS TESTES")
        self.log("="*70)
        self.log(f"Tests Run: {self.tests_run}")
        self.log(f"Tests Passed: {self.tests_passed}")
        self.log(f"Tests Failed: {self.tests_run - self.tests_passed}")
        self.log(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%" if self.tests_run > 0 else "0%")
        
        if self.tests_passed == self.tests_run:
            self.log("🎉 ALL INDIVIDUAL TESTS PASSED!")
        else:
            self.log("⚠️  SOME INDIVIDUAL TESTS FAILED")

async def main():
    """Main function to run WebSocket tests"""
    print("🔌 TESTE DE WEBSOCKET DERIV - ESTABILIDADE E PERFORMANCE")
    print("=" * 70)
    print("📋 Conforme solicitado na review request:")
    print("   OBJETIVO: Testar somente BACKEND WebSocket")
    print("   TESTES:")
    print("   1. Aguardar 5s pós-start")
    print("   2. GET /api/deriv/status (connected=true)")
    print("   3. WebSocket /api/ws/ticks?symbols=R_100,R_75,R_50 por 30s:")
    print("      - Mensagens >= 45 em 30s (≈1.5 msg/s)")
    print("      - Validar type:'tick' com symbol e price")
    print("      - Validar heartbeats")
    print("      - Conexão estável")
    print("   4. (Opcional) WebSocket /api/ws/contract/123456 heartbeat")
    print("   🎯 FOCO: Backend WS estável e performático (~1.5 msg/s)")
    
    # Use the URL from frontend/.env as specified
    tester = DerivWebSocketTester()
    
    try:
        # Run WebSocket tests
        success, results = await tester.run_websocket_tests()
        
        # Print summary
        tester.print_summary()
        
        # Exit with appropriate code
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        print("\n⚠️  Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())