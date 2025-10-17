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
4. GET /api/ml/status - verificar estado dos modelos ML

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
from urllib.parse import urlparse

class DerivConnectivityTester:
    def __init__(self, base_url="https://derivbot-upgrade.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
        self.tests_run = 0
        self.tests_passed = 0
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})
        
        # WebSocket URL (convert https to wss)
        parsed = urlparse(base_url)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        self.ws_url = f"{ws_scheme}://{parsed.netloc}/api/ws"

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
        self.log("📋 Objetivo: GET /api/deriv/status - verificar conectividade com Deriv")
        
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
        environment = data.get('environment', 'unknown')
        symbols = data.get('symbols', [])
        last_heartbeat = data.get('last_heartbeat')
        
        self.log(f"📊 RESULTADOS DA CONECTIVIDADE DERIV:")
        self.log(f"   Conectado: {connected}")
        self.log(f"   Autenticado: {authenticated}")
        self.log(f"   Ambiente: {environment}")
        self.log(f"   Símbolos subscritos: {len(symbols)} - {symbols}")
        self.log(f"   Último heartbeat: {last_heartbeat}")
        
        # Validation
        if not connected:
            self.log("❌ CRITICAL: Deriv não está conectado!")
            self.log("   Problema: WebSocket com Deriv não estabelecido")
            return False, {"message": "deriv_not_connected", "data": data}
        
        if environment != "DEMO":
            self.log(f"⚠️  WARNING: Ambiente não é DEMO: {environment}")
            self.log("   Esperado: DEMO para testes seguros")
        
        if not authenticated:
            self.log("⚠️  INFO: Deriv não autenticado (modo anônimo)")
            self.log("   Funcionalidades limitadas disponíveis")
        
        self.log("✅ Deriv conectado com sucesso!")
        return True, data

    def test_strategy_status(self):
        """Test 2: GET /api/strategy/status - verificar estado do strategy runner"""
        self.log("\n" + "="*70)
        self.log("TEST 2: VERIFICAR ESTADO DO STRATEGY RUNNER")
        self.log("="*70)
        self.log("📋 Objetivo: GET /api/strategy/status - verificar estado do strategy runner")
        
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
        config = data.get('config')
        last_run_at = data.get('last_run_at')
        today_pnl = data.get('today_pnl', 0)
        today_trades = data.get('today_trades', 0)
        total_trades = data.get('total_trades', 0)
        wins = data.get('wins', 0)
        losses = data.get('losses', 0)
        win_rate = data.get('win_rate', 0)
        daily_pnl = data.get('daily_pnl', 0)
        global_daily_pnl = data.get('global_daily_pnl', 0)
        
        self.log(f"📊 RESULTADOS DO STRATEGY RUNNER:")
        self.log(f"   Executando: {running}")
        self.log(f"   Configuração: {'Definida' if config else 'Não definida'}")
        self.log(f"   Última execução: {last_run_at}")
        self.log(f"   PnL hoje: {today_pnl}")
        self.log(f"   Trades hoje: {today_trades}")
        self.log(f"   Total trades: {total_trades}")
        self.log(f"   Vitórias: {wins}")
        self.log(f"   Perdas: {losses}")
        self.log(f"   Taxa de vitória: {win_rate}%")
        self.log(f"   PnL diário: {daily_pnl}")
        self.log(f"   PnL global diário: {global_daily_pnl}")
        
        # Validation
        if running:
            self.log("✅ Strategy Runner está executando")
            if last_run_at:
                current_time = int(time.time())
                time_since_last_run = current_time - last_run_at
                self.log(f"   Última execução há {time_since_last_run}s")
                if time_since_last_run > 300:  # 5 minutes
                    self.log("⚠️  WARNING: Última execução há mais de 5 minutos")
        else:
            self.log("ℹ️  Strategy Runner não está executando (normal)")
        
        # Check for consistency
        if wins + losses != total_trades:
            self.log(f"⚠️  WARNING: Inconsistência nos contadores: wins({wins}) + losses({losses}) != total({total_trades})")
        
        self.log("✅ Strategy Runner status obtido com sucesso!")
        return True, data

    async def test_websocket_ticks(self):
        """Test 3: WebSocket /api/ws/ticks - testar conexão de ticks (30s, verificar consistência)"""
        self.log("\n" + "="*70)
        self.log("TEST 3: TESTAR WEBSOCKET DE TICKS")
        self.log("="*70)
        self.log("📋 Objetivo: WebSocket /api/ws/ticks - conectar por 30s, verificar ticks consistentes")
        
        ws_url = f"{self.ws_url}/ticks?symbols=R_100,R_10"
        self.log(f"   WebSocket URL: {ws_url}")
        
        try:
            self.log("🔌 Conectando ao WebSocket...")
            
            async with websockets.connect(ws_url) as websocket:
                self.log("✅ WebSocket conectado com sucesso!")
                
                # Track received messages
                messages_received = 0
                symbols_seen = set()
                connection_stable = True
                start_time = time.time()
                test_duration = 30  # 30 seconds as requested
                
                self.log(f"📊 Monitorando por {test_duration} segundos...")
                
                try:
                    while time.time() - start_time < test_duration:
                        try:
                            # Wait for message with timeout
                            message = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                            data = json.loads(message)
                            messages_received += 1
                            
                            # Log first few messages for debugging
                            if messages_received <= 5:
                                self.log(f"   Mensagem {messages_received}: {json.dumps(data, indent=2)}")
                            
                            # Track symbols if it's a tick message
                            if data.get('type') == 'tick':
                                symbol = data.get('symbol')
                                if symbol:
                                    symbols_seen.add(symbol)
                            
                            # Log progress every 10 seconds
                            elapsed = time.time() - start_time
                            if messages_received % 50 == 0 or int(elapsed) % 10 == 0:
                                self.log(f"   Progresso: {elapsed:.1f}s - {messages_received} mensagens recebidas")
                        
                        except asyncio.TimeoutError:
                            # No message received in 2 seconds
                            elapsed = time.time() - start_time
                            self.log(f"   ⚠️  Timeout aguardando mensagem após {elapsed:.1f}s")
                            if elapsed > 10:  # If no messages for more than 10s, it's a problem
                                connection_stable = False
                                break
                            continue
                        
                        except websockets.exceptions.ConnectionClosed:
                            self.log("❌ WebSocket desconectou inesperadamente!")
                            connection_stable = False
                            break
                
                except Exception as e:
                    self.log(f"❌ Erro durante monitoramento: {e}")
                    connection_stable = False
                
                # Final results
                elapsed_total = time.time() - start_time
                self.log(f"\n📊 RESULTADOS DO TESTE WEBSOCKET:")
                self.log(f"   Duração total: {elapsed_total:.1f}s")
                self.log(f"   Mensagens recebidas: {messages_received}")
                self.log(f"   Símbolos vistos: {list(symbols_seen)}")
                self.log(f"   Conexão estável: {connection_stable}")
                
                if messages_received > 0:
                    rate = messages_received / elapsed_total
                    self.log(f"   Taxa de mensagens: {rate:.2f} msg/s")
                
                # Validation
                if not connection_stable:
                    self.log("❌ FAILED: WebSocket não manteve conexão estável")
                    return False, {
                        "stable": False,
                        "messages_received": messages_received,
                        "duration": elapsed_total,
                        "symbols_seen": list(symbols_seen)
                    }
                
                if messages_received == 0:
                    self.log("❌ FAILED: Nenhuma mensagem recebida")
                    return False, {
                        "stable": connection_stable,
                        "messages_received": 0,
                        "duration": elapsed_total,
                        "symbols_seen": []
                    }
                
                if len(symbols_seen) == 0:
                    self.log("⚠️  WARNING: Nenhum tick de símbolo recebido")
                
                self.log("✅ WebSocket de ticks funcionando corretamente!")
                self.tests_passed += 1
                return True, {
                    "stable": connection_stable,
                    "messages_received": messages_received,
                    "duration": elapsed_total,
                    "symbols_seen": list(symbols_seen),
                    "message_rate": messages_received / elapsed_total if elapsed_total > 0 else 0
                }
        
        except websockets.exceptions.InvalidURI:
            self.log(f"❌ FAILED: URL WebSocket inválida: {ws_url}")
            return False, {"error": "invalid_uri"}
        
        except websockets.exceptions.ConnectionClosed:
            self.log("❌ FAILED: WebSocket fechou durante conexão inicial")
            return False, {"error": "connection_closed"}
        
        except asyncio.TimeoutError:
            self.log("❌ FAILED: Timeout conectando ao WebSocket")
            return False, {"error": "connection_timeout"}
        
        except Exception as e:
            self.log(f"❌ FAILED: Erro inesperado no WebSocket: {e}")
            return False, {"error": str(e)}

    def test_ml_status(self):
        """Test 4: GET /api/ml/status - verificar estado dos modelos ML"""
        self.log("\n" + "="*70)
        self.log("TEST 4: VERIFICAR ESTADO DOS MODELOS ML")
        self.log("="*70)
        self.log("📋 Objetivo: GET /api/ml/status - verificar estado dos modelos ML")
        
        success, data, status_code = self.run_test(
            "ML Status Check",
            "GET",
            "ml/status",
            200
        )
        
        if not success:
            self.log(f"❌ CRITICAL: GET /api/ml/status falhou - Status: {status_code}")
            return False, data
        
        # Check if it's a "no champion" message or actual model data
        if isinstance(data, dict) and 'message' in data:
            message = data.get('message', '')
            self.log(f"📊 RESULTADO ML STATUS:")
            self.log(f"   Mensagem: {message}")
            
            if 'no champion' in message.lower():
                self.log("ℹ️  Nenhum modelo campeão definido (estado inicial válido)")
                self.log("   Sistema ML está funcionando mas sem modelo ativo")
                return True, data
            else:
                self.log(f"ℹ️  Mensagem do sistema ML: {message}")
                return True, data
        
        # If it's actual model data
        model_id = data.get('model_id')
        symbol = data.get('symbol')
        timeframe = data.get('timeframe')
        model_type = data.get('model_type')
        features_count = data.get('features_count', 0)
        metrics = data.get('metrics', {})
        
        self.log(f"📊 RESULTADOS DO MODELO ML:")
        self.log(f"   Model ID: {model_id}")
        self.log(f"   Símbolo: {symbol}")
        self.log(f"   Timeframe: {timeframe}")
        self.log(f"   Tipo: {model_type}")
        self.log(f"   Features: {features_count}")
        
        if metrics:
            precision = metrics.get('precision', 0)
            recall = metrics.get('recall', 0)
            f1_score = metrics.get('f1_score', 0)
            accuracy = metrics.get('accuracy', 0)
            
            self.log(f"   Métricas:")
            if precision is not None:
                self.log(f"     Precision: {precision:.3f}")
            if recall is not None:
                self.log(f"     Recall: {recall:.3f}")
            if f1_score is not None:
                self.log(f"     F1-Score: {f1_score:.3f}")
            if accuracy is not None:
                self.log(f"     Accuracy: {accuracy:.3f}")
        
        # Validation
        if model_id:
            self.log("✅ Modelo ML campeão encontrado!")
            if features_count > 0:
                self.log(f"   ✅ Modelo tem {features_count} features")
            else:
                self.log("   ⚠️  WARNING: Modelo sem features definidas")
        
        self.log("✅ ML Status obtido com sucesso!")
        return True, data

    async def run_comprehensive_tests(self):
        """Run all connectivity tests as requested"""
        self.log("\n" + "🚀" + "="*68)
        self.log("TESTES DE CONECTIVIDADE BÁSICA DO BOT DE TRADING DERIV")
        self.log("🚀" + "="*68)
        self.log("📋 Conforme solicitado na review request:")
        self.log("   1. GET /api/deriv/status - verificar conectividade com Deriv")
        self.log("   2. GET /api/strategy/status - verificar estado do strategy runner")
        self.log("   3. WebSocket /api/ws/ticks - testar conexão de ticks (30s)")
        self.log("   4. GET /api/ml/status - verificar estado dos modelos ML")
        self.log("   ⚠️  IMPORTANTE: Conta DEMO, NÃO executar trades reais")
        self.log(f"   🌐 Base URL: {self.base_url}")
        
        results = {}
        
        # Test 1: Deriv Status
        self.log("\n🔍 EXECUTANDO TESTE 1: Conectividade Deriv")
        deriv_ok, deriv_data = self.test_deriv_status()
        results['deriv_status'] = deriv_ok
        
        # Test 2: Strategy Status
        self.log("\n🔍 EXECUTANDO TESTE 2: Estado Strategy Runner")
        strategy_ok, strategy_data = self.test_strategy_status()
        results['strategy_status'] = strategy_ok
        
        # Test 3: WebSocket Ticks (async)
        self.log("\n🔍 EXECUTANDO TESTE 3: WebSocket Ticks")
        self.tests_run += 1  # Manual increment since async test doesn't use run_test
        try:
            ws_ok, ws_data = await self.test_websocket_ticks()
            results['websocket_ticks'] = ws_ok
        except Exception as e:
            self.log(f"❌ FAILED - WebSocket Test - Error: {e}")
            ws_ok = False
            ws_data = {"error": str(e)}
            results['websocket_ticks'] = False
        
        # Test 4: ML Status
        self.log("\n🔍 EXECUTANDO TESTE 4: Estado Modelos ML")
        ml_ok, ml_data = self.test_ml_status()
        results['ml_status'] = ml_ok
        
        # Final Summary
        self.log("\n" + "🏁" + "="*68)
        self.log("RESUMO FINAL DOS TESTES DE CONECTIVIDADE")
        self.log("🏁" + "="*68)
        
        if deriv_ok:
            connected = deriv_data.get('connected', False) if isinstance(deriv_data, dict) else False
            authenticated = deriv_data.get('authenticated', False) if isinstance(deriv_data, dict) else False
            self.log(f"✅ 1. Deriv Status: Conectado={connected}, Auth={authenticated} ✓")
        else:
            self.log("❌ 1. Deriv Status: FAILED")
        
        if strategy_ok:
            running = strategy_data.get('running', False) if isinstance(strategy_data, dict) else False
            total_trades = strategy_data.get('total_trades', 0) if isinstance(strategy_data, dict) else 0
            self.log(f"✅ 2. Strategy Status: Running={running}, Trades={total_trades} ✓")
        else:
            self.log("❌ 2. Strategy Status: FAILED")
        
        if ws_ok:
            messages = ws_data.get('messages_received', 0) if isinstance(ws_data, dict) else 0
            stable = ws_data.get('stable', False) if isinstance(ws_data, dict) else False
            self.log(f"✅ 3. WebSocket Ticks: {messages} mensagens, Estável={stable} ✓")
        else:
            self.log("❌ 3. WebSocket Ticks: FAILED")
        
        if ml_ok:
            if isinstance(ml_data, dict):
                if 'message' in ml_data:
                    message = ml_data.get('message', '')
                    self.log(f"✅ 4. ML Status: {message} ✓")
                else:
                    model_id = ml_data.get('model_id', 'N/A')
                    self.log(f"✅ 4. ML Status: Modelo {model_id} ativo ✓")
            else:
                self.log("✅ 4. ML Status: OK ✓")
        else:
            self.log("❌ 4. ML Status: FAILED")
        
        # Overall assessment
        critical_tests_passed = deriv_ok and strategy_ok and ml_ok
        all_tests_passed = critical_tests_passed and ws_ok
        
        if all_tests_passed:
            self.log("\n🎉 TODOS OS TESTES DE CONECTIVIDADE PASSARAM!")
            self.log("📋 Sistema de trading funcionando perfeitamente:")
            self.log("   ✅ Deriv conectado e funcionando")
            self.log("   ✅ Strategy Runner operacional")
            self.log("   ✅ WebSocket de ticks estável")
            self.log("   ✅ Sistema ML funcionando")
            self.log("   🚀 Bot pronto para operação!")
        elif critical_tests_passed:
            self.log("\n🎉 TESTES CRÍTICOS PASSARAM!")
            self.log("📋 Funcionalidades principais funcionando:")
            self.log("   ✅ APIs principais operacionais")
            if not ws_ok:
                self.log("   ⚠️  WebSocket precisa de verificação")
                self.log("   📋 Possível causa dos problemas de desconexão reportados")
        else:
            failed_tests = []
            if not deriv_ok:
                failed_tests.append("Deriv Status")
            if not strategy_ok:
                failed_tests.append("Strategy Status")
            if not ml_ok:
                failed_tests.append("ML Status")
            
            self.log(f"\n⚠️  {len(failed_tests)} TESTE(S) CRÍTICO(S) FALHARAM: {', '.join(failed_tests)}")
            self.log("📋 Verificar logs detalhados acima para diagnóstico")
            
            if not deriv_ok:
                self.log("   🔧 Deriv: Verificar DERIV_APP_ID e DERIV_API_TOKEN")
            if not strategy_ok:
                self.log("   🔧 Strategy: Verificar se backend está rodando corretamente")
            if not ml_ok:
                self.log("   🔧 ML: Verificar se modelos foram treinados")
        
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
    print("📋 Testing basic connectivity as requested:")
    print("   1. Deriv API connectivity")
    print("   2. Strategy Runner status")
    print("   3. WebSocket ticks stability (30s test)")
    print("   4. ML system status")
    print("   ⚠️  DEMO account only, no real trades")
    
    # Use the URL from frontend/.env as specified
    tester = DerivConnectivityTester()
    
    try:
        # Run comprehensive tests
        success, results = await tester.run_comprehensive_tests()
        
        # Print summary
        tester.print_summary()
        
        # Exit with appropriate code
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        print("\n⚠️  Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())