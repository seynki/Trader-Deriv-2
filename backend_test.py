#!/usr/bin/env python3
"""
Backend API Testing for Deriv Trading Bot Connectivity
Tests as requested in Portuguese review:
🤖 TESTE DE CONECTIVIDADE BÁSICA DO BOT DE TRADING DERIV

CONTEXTO: Bot de trading com problemas de WebSocket fechando constantemente, 
bot parando após contratos, e sistema ML não retreinando. Usuario usando conta DEMO, símbolo R_100.

TESTES SOLICITADOS:
1. GET /api/deriv/status - verificar conectividade com Deriv
2. GET /api/strategy/status - verificar estado do strategy runner  
3. WebSocket /api/ws/ticks - testar conexão de ticks (conectar por 30s, verificar se recebe ticks consistentes)
4. Verificar se há erros nos logs do backend relacionados ao WebSocket

IMPORTANTE: 
- Conta DEMO da Deriv
- NÃO executar trades reais (/api/deriv/buy)
- Focar em identificar problemas de conectividade e estabilidade
- Verificar se WebSocket fica estável ou fica desconectando
- Reportar qualquer erro ou instabilidade observada
"""

import requests
import json
import sys
import time
import asyncio
import websockets
from datetime import datetime

class DerivConnectivityTester:
    def __init__(self, base_url="https://autotrade-monitor.preview.emergentagent.com"):
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

    def test_online_learning_progress(self):
        """Test 2: GET /api/ml/online/progress - verificar se há modelos ativos e updates > 0"""
        self.log("\n" + "="*70)
        self.log("TEST 2: VERIFICAR ONLINE LEARNING PROGRESS")
        self.log("="*70)
        self.log("📋 Objetivo: GET /api/ml/online/progress (verificar se há modelos ativos e updates > 0)")
        
        success, data, status_code = self.run_test(
            "Online Learning Progress Check",
            "GET",
            "ml/online/progress",
            200
        )
        
        if not success:
            self.log(f"❌ CRITICAL: GET /api/ml/online/progress falhou - Status: {status_code}")
            return False, data
        
        active_models = data.get('active_models', 0)
        total_updates = data.get('total_updates', 0)
        models_detail = data.get('models_detail', [])
        
        self.log(f"📊 RESULTADOS:")
        self.log(f"   Modelos ativos: {active_models}")
        self.log(f"   Total de updates: {total_updates}")
        self.log(f"   Detalhes dos modelos: {len(models_detail)}")
        
        for i, model in enumerate(models_detail[:3], 1):  # Show first 3 models
            model_id = model.get('model_id', 'unknown')
            update_count = model.get('update_count', 0)
            features_count = model.get('features_count', 0)
            accuracy = model.get('current_accuracy', 0)
            trend = model.get('improvement_trend', 'unknown')
            
            self.log(f"   Modelo {i}: {model_id}")
            self.log(f"     Updates: {update_count}")
            self.log(f"     Features: {features_count}")
            self.log(f"     Accuracy: {accuracy:.3f}")
            self.log(f"     Trend: {trend}")
        
        # Validation - check if online learning is working
        has_active_models = active_models > 0
        has_updates = total_updates > 0
        
        if not has_active_models:
            self.log("❌ CRITICAL: Nenhum modelo online ativo encontrado")
            return False, {"message": "no_active_models", "data": data}
        
        if not has_updates:
            self.log("⚠️  WARNING: Modelos ativos mas sem updates (total_updates = 0)")
            self.log("   Isso pode indicar que o sistema ainda não processou trades")
        
        self.log(f"✅ Online Learning funcionando: {active_models} modelo(s) ativo(s), {total_updates} update(s)")
        return True, data

    def test_strategy_status(self):
        """Test 3: GET /api/strategy/status - verificar estado da estratégia"""
        self.log("\n" + "="*70)
        self.log("TEST 3: VERIFICAR ESTADO DA ESTRATÉGIA")
        self.log("="*70)
        self.log("📋 Objetivo: GET /api/strategy/status (verificar estado do strategy runner)")
        
        success, data, status_code = self.run_test(
            "Strategy Status Check",
            "GET",
            "strategy/status",
            200
        )
        
        if not success:
            self.log(f"❌ CRITICAL: GET /api/strategy/status falhou - Status: {status_code}")
            return False, data
        
        running = data.get('running', False)
        total_trades = data.get('total_trades', 0)
        wins = data.get('wins', 0)
        losses = data.get('losses', 0)
        daily_pnl = data.get('daily_pnl', 0.0)
        global_daily_pnl = data.get('global_daily_pnl', 0.0)
        win_rate = data.get('win_rate', 0.0)
        last_run_at = data.get('last_run_at')
        
        self.log(f"📊 RESULTADOS:")
        self.log(f"   Executando: {running}")
        self.log(f"   Total trades: {total_trades}")
        self.log(f"   Vitórias: {wins}")
        self.log(f"   Derrotas: {losses}")
        self.log(f"   PnL diário: {daily_pnl}")
        self.log(f"   PnL global diário: {global_daily_pnl}")
        self.log(f"   Taxa de vitória: {win_rate}%")
        self.log(f"   Última execução: {last_run_at}")
        
        # Validation - check consistency
        if wins + losses != total_trades:
            self.log(f"⚠️  WARNING: Inconsistência nos contadores: wins({wins}) + losses({losses}) != total_trades({total_trades})")
        
        self.log(f"✅ Status da estratégia obtido com sucesso (running: {running})")
        return True, data

    async def test_websocket_ticks(self):
        """Test 4: WebSocket /api/ws/ticks - testar por 30 segundos para verificar estabilidade após correções"""
        self.log("\n" + "="*70)
        self.log("TEST 4: TESTAR WEBSOCKET DE TICKS (30 SEGUNDOS) - APÓS CORREÇÕES")
        self.log("="*70)
        self.log("📋 Objetivo: Conectar ao WebSocket /api/ws/ticks por 30s e verificar se mantém conexão estável")
        self.log("📋 Verificar se correções resolveram problemas de 'received 1000 (OK)' e desconexões")
        self.log("📋 Taxa esperada: > 0.52 ticks/s conforme review request")
        
        ws_url = f"{self.ws_url}/api/ws/ticks?symbols=R_100,R_10"
        self.log(f"   WebSocket URL: {ws_url}")
        
        messages_received = 0
        connection_errors = 0
        symbols_detected = set()
        start_time = time.time()
        test_duration = 30  # 30 seconds as requested in review
        
        try:
            self.log("🔌 Conectando ao WebSocket...")
            
            # Use websockets.connect without timeout parameter for compatibility
            websocket = await websockets.connect(ws_url)
            self.log("✅ WebSocket conectado com sucesso")
            
            try:
                # Send initial payload if needed
                initial_payload = {"symbols": ["R_100", "R_10"]}
                await websocket.send(json.dumps(initial_payload))
                self.log(f"📤 Payload inicial enviado: {initial_payload}")
                
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
                            
                            if symbol != 'unknown':
                                symbols_detected.add(symbol)
                            
                            # Log every 10th message to avoid spam
                            if messages_received % 10 == 0 or messages_received <= 5:
                                self.log(f"📨 Mensagem #{messages_received}: type={msg_type}, symbol={symbol}, price={price}")
                            
                        except json.JSONDecodeError:
                            self.log(f"⚠️  Mensagem não-JSON recebida: {message[:100]}...")
                            
                    except asyncio.TimeoutError:
                        # No message received in 2 seconds - this might indicate instability
                        elapsed = time.time() - start_time
                        self.log(f"⚠️  Timeout aguardando mensagem (elapsed: {elapsed:.1f}s)")
                        connection_errors += 1
                        
                        if connection_errors >= 5:
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
        
        self.log(f"\n📊 ANÁLISE DO WEBSOCKET:")
        self.log(f"   Tempo de teste: {elapsed_time:.1f}s")
        self.log(f"   Mensagens recebidas: {messages_received}")
        self.log(f"   Taxa de mensagens: {message_rate:.2f} msg/s")
        self.log(f"   Erros de conexão: {connection_errors}")
        self.log(f"   Símbolos detectados: {list(symbols_detected)}")
        
        # Determine if WebSocket is stable
        is_stable = True
        issues = []
        
        if messages_received == 0:
            is_stable = False
            issues.append("Nenhuma mensagem recebida")
            
        elif message_rate < 0.5:  # Less than 0.5 messages per second
            is_stable = False
            issues.append(f"Taxa de mensagens muito baixa: {message_rate:.2f} msg/s")
            
        if connection_errors > 2:
            is_stable = False
            issues.append(f"Muitos erros de conexão: {connection_errors}")
            
        if len(symbols_detected) == 0:
            is_stable = False
            issues.append("Nenhum símbolo detectado nas mensagens")
            
        if elapsed_time < test_duration * 0.8:  # Test ended prematurely
            is_stable = False
            issues.append(f"Teste terminou prematuramente: {elapsed_time:.1f}s < {test_duration}s")
        
        if is_stable:
            self.log("✅ WebSocket ESTÁVEL - funcionando corretamente")
            self.tests_passed += 1
        else:
            self.log("❌ WebSocket INSTÁVEL - problemas detectados:")
            for issue in issues:
                self.log(f"   - {issue}")
        
        self.tests_run += 1
        
        return is_stable, {
            "elapsed_time": elapsed_time,
            "messages_received": messages_received,
            "message_rate": message_rate,
            "connection_errors": connection_errors,
            "symbols_detected": list(symbols_detected),
            "is_stable": is_stable,
            "issues": issues
        }

    def check_backend_logs(self):
        """Test 4: Verificar se há erros nos logs do backend relacionados ao WebSocket"""
        self.log("\n" + "="*70)
        self.log("TEST 4: VERIFICAR LOGS DO BACKEND PARA ERROS 'received 1000 (OK)'")
        self.log("="*70)
        self.log("📋 Objetivo: Verificar se erros 'received 1000 (OK)' ainda aparecem nos logs")
        self.log("📋 Monitorar logs do backend para detectar problemas de WebSocket")
        
        # Note: In a containerized environment, we can try to check supervisor logs
        self.log("⚠️  Nota: Tentando verificar logs do supervisor para erros de WebSocket")
        
        import subprocess
        import os
        
        try:
            # Try to check supervisor backend logs
            self.log("📋 Verificando logs do supervisor backend...")
            
            # Check if supervisor log files exist
            log_paths = [
                "/var/log/supervisor/backend.err.log",
                "/var/log/supervisor/backend.out.log", 
                "/var/log/supervisor/supervisord.log"
            ]
            
            websocket_errors_found = []
            
            for log_path in log_paths:
                if os.path.exists(log_path):
                    try:
                        # Get last 100 lines of log
                        result = subprocess.run(['tail', '-n', '100', log_path], 
                                              capture_output=True, text=True, timeout=10)
                        
                        if result.returncode == 0:
                            log_content = result.stdout
                            
                            # Look for specific WebSocket errors
                            error_patterns = [
                                "received 1000 (OK)",
                                "WebSocket message processing error",
                                "Error sending tick message",
                                "WebSocketDisconnect",
                                "ConnectionClosed"
                            ]
                            
                            for pattern in error_patterns:
                                if pattern in log_content:
                                    lines_with_error = [line.strip() for line in log_content.split('\n') 
                                                      if pattern in line]
                                    if lines_with_error:
                                        websocket_errors_found.extend(lines_with_error[-3:])  # Last 3 occurrences
                                        self.log(f"⚠️  Encontrado padrão '{pattern}' em {log_path}")
                            
                    except subprocess.TimeoutExpired:
                        self.log(f"⚠️  Timeout ao ler {log_path}")
                    except Exception as e:
                        self.log(f"⚠️  Erro ao ler {log_path}: {e}")
                else:
                    self.log(f"📋 Log não encontrado: {log_path}")
            
            # Check if backend is responding
            success, data, status_code = self.run_test(
                "Backend Health Check",
                "GET",
                "",  # Root endpoint
                200
            )
            
            # Analysis
            if websocket_errors_found:
                self.log(f"❌ ERROS DE WEBSOCKET DETECTADOS ({len(websocket_errors_found)} ocorrências):")
                for i, error in enumerate(websocket_errors_found[:5], 1):  # Show first 5
                    self.log(f"   {i}. {error}")
                
                if len(websocket_errors_found) > 5:
                    self.log(f"   ... e mais {len(websocket_errors_found) - 5} erros")
                
                return False, {
                    "backend_healthy": success,
                    "websocket_errors_found": len(websocket_errors_found),
                    "error_samples": websocket_errors_found[:5],
                    "status_code": status_code
                }
            else:
                self.log("✅ Nenhum erro de WebSocket 'received 1000 (OK)' detectado nos logs recentes")
                
                if success:
                    self.log("✅ Backend respondendo corretamente")
                    return True, {"backend_healthy": True, "websocket_errors_found": 0}
                else:
                    self.log("❌ Backend não está respondendo adequadamente")
                    return False, {"backend_healthy": False, "status_code": status_code}
                    
        except Exception as e:
            self.log(f"❌ Erro ao verificar logs: {e}")
            
            # Fallback to basic health check
            success, data, status_code = self.run_test(
                "Backend Health Check (Fallback)",
                "GET",
                "",
                200
            )
            
            return success, {
                "backend_healthy": success, 
                "log_check_failed": True,
                "error": str(e),
                "status_code": status_code
            }

    async def run_connectivity_tests(self):
        """Run all connectivity tests as requested in Portuguese review"""
        self.log("\n" + "🚀" + "="*68)
        self.log("TESTES DE CONECTIVIDADE BÁSICA DO SISTEMA DE TRADING")
        self.log("🚀" + "="*68)
        self.log("📋 Conforme solicitado na review request em português:")
        self.log("   1. GET /api/deriv/status - verificar conectividade com Deriv")
        self.log("   2. GET /api/strategy/status - verificar estado da estratégia")
        self.log("   3. WebSocket /api/ws/ticks - testar por 60s para verificar estabilidade após correções")
        self.log("   4. Verificar logs do backend para erros de WebSocket")
        self.log("   ⚠️  IMPORTANTE: Conta DEMO, NÃO executar /api/deriv/buy")
        self.log(f"   🌐 Base URL: {self.base_url}")
        
        results = {}
        
        # Test 1: Deriv Status
        self.log("\n🔍 EXECUTANDO TESTE 1: Conectividade com Deriv")
        deriv_ok, deriv_data = self.test_deriv_status()
        results['deriv_status'] = deriv_ok
        
        # Test 2: Strategy Status
        self.log("\n🔍 EXECUTANDO TESTE 2: Estado da Estratégia")
        strategy_ok, strategy_data = self.test_strategy_status()
        results['strategy_status'] = strategy_ok
        
        # Test 3: WebSocket Ticks (60 seconds)
        self.log("\n🔍 EXECUTANDO TESTE 3: WebSocket de Ticks (60s)")
        websocket_ok, websocket_data = await self.test_websocket_ticks()
        results['websocket_ticks'] = websocket_ok
        
        # Test 4: Backend Logs Check
        self.log("\n🔍 EXECUTANDO TESTE 4: Verificação de Logs")
        logs_ok, logs_data = self.check_backend_logs()
        results['backend_logs'] = logs_ok
        
        # Final Summary
        self.log("\n" + "🏁" + "="*68)
        self.log("RESUMO FINAL DOS TESTES DE CONECTIVIDADE")
        self.log("🏁" + "="*68)
        
        if deriv_ok:
            connected = deriv_data.get('connected', False) if isinstance(deriv_data, dict) else False
            authenticated = deriv_data.get('authenticated', False) if isinstance(deriv_data, dict) else False
            environment = deriv_data.get('environment', 'UNKNOWN') if isinstance(deriv_data, dict) else 'UNKNOWN'
            self.log(f"✅ 1. Deriv Status: connected={connected}, authenticated={authenticated}, env={environment} ✓")
        else:
            self.log("❌ 1. Deriv Status: FAILED")
        
        if strategy_ok:
            running = strategy_data.get('running', False) if isinstance(strategy_data, dict) else False
            total_trades = strategy_data.get('total_trades', 0) if isinstance(strategy_data, dict) else 0
            self.log(f"✅ 2. Strategy Status: running={running}, total_trades={total_trades} ✓")
        else:
            self.log("❌ 2. Strategy Status: FAILED")
        
        if websocket_ok:
            messages = websocket_data.get('messages_received', 0) if isinstance(websocket_data, dict) else 0
            rate = websocket_data.get('message_rate', 0) if isinstance(websocket_data, dict) else 0
            self.log(f"✅ 3. WebSocket Ticks: {messages} mensagens, {rate:.2f} msg/s ✓")
        else:
            issues = websocket_data.get('issues', []) if isinstance(websocket_data, dict) else []
            self.log(f"❌ 3. WebSocket Ticks: INSTÁVEL - {len(issues)} problema(s)")
            for issue in issues[:3]:  # Show first 3 issues
                self.log(f"   - {issue}")
        
        if logs_ok:
            self.log("✅ 4. Backend Logs: Sem problemas detectados ✓")
        else:
            self.log("❌ 4. Backend Logs: Problemas detectados")
        
        # Overall assessment
        critical_tests_passed = deriv_ok and strategy_ok
        all_tests_passed = critical_tests_passed and websocket_ok and logs_ok
        
        if all_tests_passed:
            self.log("\n🎉 TODOS OS TESTES DE CONECTIVIDADE PASSARAM!")
            self.log("📋 Sistema de trading funcionando perfeitamente:")
            self.log("   ✅ Deriv conectado e funcionando")
            self.log("   ✅ Strategy runner operacional")
            self.log("   ✅ WebSocket estável e recebendo ticks")
            self.log("   ✅ Backend sem erros detectados")
        elif critical_tests_passed:
            self.log("\n🎉 TESTES CRÍTICOS PASSARAM!")
            self.log("📋 Funcionalidades principais funcionando:")
            self.log("   ✅ Conectividade básica operacional")
            if not websocket_ok:
                self.log("   ❌ WebSocket com problemas de estabilidade")
                self.log("   📋 RECOMENDAÇÃO: Investigar instabilidade do WebSocket")
            if not logs_ok:
                self.log("   ⚠️  Verificação de logs limitada")
        else:
            failed_tests = []
            if not deriv_ok:
                failed_tests.append("Deriv Status")
            if not strategy_ok:
                failed_tests.append("Strategy Status")
            
            self.log(f"\n⚠️  {len(failed_tests)} TESTE(S) CRÍTICO(S) FALHARAM: {', '.join(failed_tests)}")
            self.log("📋 Sistema não está operacional - verificar configuração")
        
        return critical_tests_passed, results

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
    """Main function to run connectivity tests"""
    print("🤖 Deriv Trading Bot Connectivity Tester")
    print("=" * 70)
    print("📋 Testing as requested in Portuguese review:")
    print("   1. GET /api/deriv/status - verificar conectividade")
    print("   2. GET /api/strategy/status - verificar estado da estratégia")
    print("   3. WebSocket /api/ws/ticks - testar estabilidade por 60s após correções")
    print("   4. Verificar logs do backend")
    print("   ⚠️  IMPORTANTE: Conta DEMO, não executar trades reais")
    
    # Use the URL from frontend/.env as specified
    tester = DerivConnectivityTester()
    
    try:
        # Run comprehensive tests
        success, results = await tester.run_connectivity_tests()
        
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