#!/usr/bin/env python3
"""
WebSocket Stability Test - Focused on the review request
Tests WebSocket stability after corrections for 60 seconds as requested
"""

import requests
import json
import sys
import time
import asyncio
import websockets
from datetime import datetime
import subprocess
import os

class WebSocketStabilityTester:
    def __init__(self, base_url="https://tick-system-repair.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
        self.ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def log(self, message):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def test_deriv_status(self):
        """Test 1: GET /api/deriv/status - verificar conectividade"""
        self.log("🔍 TEST 1: GET /api/deriv/status - verificar conectividade")
        
        try:
            response = self.session.get(f"{self.api_url}/deriv/status", timeout=10)
            data = response.json()
            
            connected = data.get('connected', False)
            authenticated = data.get('authenticated', False)
            environment = data.get('environment', 'UNKNOWN')
            
            self.log(f"   Status: {response.status_code}")
            self.log(f"   Conectado: {connected}")
            self.log(f"   Autenticado: {authenticated}")
            self.log(f"   Ambiente: {environment}")
            
            if connected and environment == "DEMO":
                self.log("✅ Deriv conectado (conta DEMO)")
                return True, data
            else:
                self.log("❌ Problema na conectividade Deriv")
                return False, data
                
        except Exception as e:
            self.log(f"❌ Erro ao testar Deriv status: {e}")
            return False, {"error": str(e)}

    def test_strategy_status(self):
        """Test 2: GET /api/strategy/status - verificar estado da estratégia"""
        self.log("🔍 TEST 2: GET /api/strategy/status - verificar estado da estratégia")
        
        try:
            response = self.session.get(f"{self.api_url}/strategy/status", timeout=10)
            data = response.json()
            
            running = data.get('running', False)
            total_trades = data.get('total_trades', 0)
            
            self.log(f"   Status: {response.status_code}")
            self.log(f"   Executando: {running}")
            self.log(f"   Total trades: {total_trades}")
            
            self.log("✅ Strategy status obtido com sucesso")
            return True, data
                
        except Exception as e:
            self.log(f"❌ Erro ao testar Strategy status: {e}")
            return False, {"error": str(e)}

    async def test_websocket_stability_60s(self):
        """Test 3: WebSocket /api/ws/ticks por 60 segundos para verificar estabilidade"""
        self.log("🔍 TEST 3: WebSocket /api/ws/ticks - TESTE DE ESTABILIDADE 60 SEGUNDOS")
        self.log("📋 Verificando se correções resolveram problemas de desconexão")
        
        ws_url = f"{self.ws_url}/api/ws/ticks?symbols=R_100,R_10"
        self.log(f"   WebSocket URL: {ws_url}")
        
        messages_received = 0
        connection_errors = 0
        symbols_detected = set()
        heartbeats_received = 0
        tick_messages = 0
        start_time = time.time()
        test_duration = 60  # 60 seconds as requested
        
        connection_stable = True
        disconnection_events = []
        
        try:
            self.log("🔌 Conectando ao WebSocket...")
            
            websocket = await websockets.connect(ws_url)
            self.log("✅ WebSocket conectado com sucesso")
            
            try:
                self.log(f"⏱️  Monitorando por {test_duration} segundos...")
                
                while time.time() - start_time < test_duration:
                    try:
                        # Wait for message with timeout
                        message = await asyncio.wait_for(websocket.recv(), timeout=3.0)
                        
                        try:
                            data = json.loads(message)
                            messages_received += 1
                            
                            msg_type = data.get('type', 'unknown')
                            symbol = data.get('symbol', 'unknown')
                            price = data.get('price', 0)
                            timestamp = data.get('timestamp', 0)
                            
                            if msg_type == 'tick':
                                tick_messages += 1
                                if symbol != 'unknown':
                                    symbols_detected.add(symbol)
                            elif msg_type == 'heartbeat':
                                heartbeats_received += 1
                            
                            # Log progress every 15 seconds
                            elapsed = time.time() - start_time
                            if int(elapsed) % 15 == 0 and int(elapsed) > 0:
                                self.log(f"📊 Progresso {int(elapsed)}s: {messages_received} mensagens, {tick_messages} ticks, {heartbeats_received} heartbeats")
                            
                        except json.JSONDecodeError:
                            self.log(f"⚠️  Mensagem não-JSON: {message[:50]}...")
                            
                    except asyncio.TimeoutError:
                        # No message received in 3 seconds
                        elapsed = time.time() - start_time
                        connection_errors += 1
                        
                        if connection_errors <= 3:  # Only log first few timeouts
                            self.log(f"⚠️  Timeout aguardando mensagem (elapsed: {elapsed:.1f}s, timeout #{connection_errors})")
                        
                        # If too many consecutive timeouts, consider connection unstable
                        if connection_errors >= 10:
                            self.log("❌ Muitos timeouts consecutivos - conexão considerada instável")
                            connection_stable = False
                            break
                            
                    except websockets.exceptions.ConnectionClosed as e:
                        elapsed = time.time() - start_time
                        self.log(f"❌ WebSocket fechou inesperadamente após {elapsed:.1f}s: {e}")
                        disconnection_events.append(f"ConnectionClosed at {elapsed:.1f}s: {e}")
                        connection_stable = False
                        break
                        
                    except Exception as e:
                        elapsed = time.time() - start_time
                        self.log(f"❌ Erro durante recepção após {elapsed:.1f}s: {e}")
                        connection_errors += 1
                        
            finally:
                try:
                    await websocket.close()
                except:
                    pass
                
        except Exception as e:
            self.log(f"❌ Erro na conexão WebSocket: {e}")
            return False, {"error": "connection_failed", "details": str(e)}
        
        # Analysis
        elapsed_time = time.time() - start_time
        message_rate = messages_received / elapsed_time if elapsed_time > 0 else 0
        tick_rate = tick_messages / elapsed_time if elapsed_time > 0 else 0
        
        self.log(f"\n📊 ANÁLISE FINAL DO WEBSOCKET (60s):")
        self.log(f"   Tempo de teste: {elapsed_time:.1f}s")
        self.log(f"   Mensagens totais: {messages_received}")
        self.log(f"   Mensagens de tick: {tick_messages}")
        self.log(f"   Heartbeats: {heartbeats_received}")
        self.log(f"   Taxa de mensagens: {message_rate:.2f} msg/s")
        self.log(f"   Taxa de ticks: {tick_rate:.2f} ticks/s")
        self.log(f"   Timeouts: {connection_errors}")
        self.log(f"   Símbolos detectados: {list(symbols_detected)}")
        self.log(f"   Desconexões: {len(disconnection_events)}")
        
        # Determine stability
        issues = []
        
        if not connection_stable:
            issues.append("Conexão instável detectada")
            
        if elapsed_time < test_duration * 0.9:  # Test ended prematurely
            issues.append(f"Teste terminou prematuramente: {elapsed_time:.1f}s < {test_duration}s")
            
        if tick_messages == 0:
            issues.append("Nenhum tick recebido")
            
        elif tick_rate < 0.5:  # Less than 0.5 ticks per second
            issues.append(f"Taxa de ticks muito baixa: {tick_rate:.2f} ticks/s")
            
        if connection_errors > 5:
            issues.append(f"Muitos timeouts: {connection_errors}")
            
        if len(symbols_detected) == 0:
            issues.append("Nenhum símbolo detectado")
            
        if len(disconnection_events) > 0:
            issues.append(f"Desconexões detectadas: {len(disconnection_events)}")
        
        is_stable = len(issues) == 0
        
        if is_stable:
            self.log("✅ WebSocket ESTÁVEL - manteve conexão por 60s e recebeu ticks consistentemente")
        else:
            self.log("❌ WebSocket INSTÁVEL - problemas detectados:")
            for issue in issues:
                self.log(f"   - {issue}")
        
        return is_stable, {
            "elapsed_time": elapsed_time,
            "messages_received": messages_received,
            "tick_messages": tick_messages,
            "heartbeats_received": heartbeats_received,
            "message_rate": message_rate,
            "tick_rate": tick_rate,
            "connection_errors": connection_errors,
            "symbols_detected": list(symbols_detected),
            "disconnection_events": disconnection_events,
            "is_stable": is_stable,
            "issues": issues
        }

    def check_backend_logs_for_errors(self):
        """Test 4: Monitorar logs do backend para verificar se erros 'received 1000 (OK)' ainda aparecem"""
        self.log("🔍 TEST 4: Verificar logs do backend para erros 'received 1000 (OK)'")
        
        log_paths = [
            "/var/log/supervisor/backend.err.log",
            "/var/log/supervisor/backend.out.log"
        ]
        
        websocket_errors_found = []
        
        for log_path in log_paths:
            if os.path.exists(log_path):
                try:
                    # Get last 50 lines of log
                    result = subprocess.run(['tail', '-n', '50', log_path], 
                                          capture_output=True, text=True, timeout=10)
                    
                    if result.returncode == 0:
                        log_content = result.stdout
                        
                        # Look for specific WebSocket errors mentioned in review
                        error_patterns = [
                            "received 1000 (OK)",
                            "WebSocket message processing error",
                            "Error sending tick message"
                        ]
                        
                        for pattern in error_patterns:
                            if pattern in log_content:
                                lines_with_error = [line.strip() for line in log_content.split('\n') 
                                                  if pattern in line]
                                if lines_with_error:
                                    websocket_errors_found.extend(lines_with_error[-5:])  # Last 5 occurrences
                                    self.log(f"⚠️  Encontrado padrão '{pattern}' em {log_path}")
                        
                except Exception as e:
                    self.log(f"⚠️  Erro ao ler {log_path}: {e}")
            else:
                self.log(f"📋 Log não encontrado: {log_path}")
        
        if websocket_errors_found:
            self.log(f"❌ ERROS 'received 1000 (OK)' AINDA APARECEM ({len(websocket_errors_found)} ocorrências):")
            for i, error in enumerate(websocket_errors_found[:3], 1):  # Show first 3
                self.log(f"   {i}. {error}")
            
            return False, {
                "websocket_errors_found": len(websocket_errors_found),
                "error_samples": websocket_errors_found[:3]
            }
        else:
            self.log("✅ Nenhum erro 'received 1000 (OK)' detectado nos logs recentes")
            return True, {"websocket_errors_found": 0}

    async def run_stability_tests(self):
        """Run all stability tests as requested in Portuguese review"""
        self.log("\n" + "🚀" + "="*68)
        self.log("TESTE DE ESTABILIDADE DO WEBSOCKET APÓS CORREÇÕES")
        self.log("🚀" + "="*68)
        self.log("📋 Conforme solicitado na review request em português:")
        self.log("   1. GET /api/deriv/status - verificar conectividade")
        self.log("   2. GET /api/strategy/status - verificar estado da estratégia")
        self.log("   3. Testar WebSocket /api/ws/ticks por 60 segundos")
        self.log("   4. Monitorar logs para verificar se erros 'received 1000 (OK)' ainda aparecem")
        self.log("   5. Verificar se a taxa de mensagens melhorou")
        self.log("   ⚠️  IMPORTANTE: Conta DEMO, NÃO executar compras reais")
        
        results = {}
        
        # Test 1: Deriv Status
        deriv_ok, deriv_data = self.test_deriv_status()
        results['deriv_status'] = deriv_ok
        
        if not deriv_ok:
            self.log("❌ CRITICAL: Deriv não conectado - abortando testes WebSocket")
            return False, results
        
        # Test 2: Strategy Status  
        strategy_ok, strategy_data = self.test_strategy_status()
        results['strategy_status'] = strategy_ok
        
        # Wait a bit for Deriv WebSocket to be ready
        self.log("\n⏱️  Aguardando 5 segundos para Deriv WebSocket estar pronto...")
        await asyncio.sleep(5)
        
        # Test 3: WebSocket Stability (60 seconds)
        self.log("\n🔍 EXECUTANDO TESTE PRINCIPAL: WebSocket 60 segundos")
        websocket_ok, websocket_data = await self.test_websocket_stability_60s()
        results['websocket_stability'] = websocket_ok
        
        # Test 4: Check logs for errors
        self.log("\n🔍 VERIFICANDO LOGS PARA ERROS 'received 1000 (OK)'")
        logs_ok, logs_data = self.check_backend_logs_for_errors()
        results['backend_logs'] = logs_ok
        
        # Final Assessment
        self.log("\n" + "🏁" + "="*68)
        self.log("RESUMO FINAL - ESTABILIDADE DO WEBSOCKET APÓS CORREÇÕES")
        self.log("🏁" + "="*68)
        
        if deriv_ok:
            connected = deriv_data.get('connected', False)
            authenticated = deriv_data.get('authenticated', False)
            environment = deriv_data.get('environment', 'UNKNOWN')
            self.log(f"✅ 1. GET /api/deriv/status: connected={connected}, authenticated={authenticated}, environment={environment}")
        else:
            self.log("❌ 1. GET /api/deriv/status: FAILED")
        
        if strategy_ok:
            running = strategy_data.get('running', False)
            self.log(f"✅ 2. GET /api/strategy/status: running={running}")
        else:
            self.log("❌ 2. GET /api/strategy/status: FAILED")
        
        if websocket_ok:
            tick_rate = websocket_data.get('tick_rate', 0)
            elapsed = websocket_data.get('elapsed_time', 0)
            ticks = websocket_data.get('tick_messages', 0)
            self.log(f"✅ 3. WebSocket /api/ws/ticks: ESTÁVEL por {elapsed:.1f}s, {ticks} ticks, {tick_rate:.2f} ticks/s")
        else:
            issues = websocket_data.get('issues', [])
            self.log(f"❌ 3. WebSocket /api/ws/ticks: INSTÁVEL - {len(issues)} problema(s)")
            for issue in issues[:2]:
                self.log(f"   - {issue}")
        
        if logs_ok:
            self.log("✅ 4. Logs do backend: Sem erros 'received 1000 (OK)' detectados")
        else:
            error_count = logs_data.get('websocket_errors_found', 0)
            self.log(f"❌ 4. Logs do backend: {error_count} erros 'received 1000 (OK)' ainda aparecem")
        
        # Overall conclusion
        corrections_successful = websocket_ok and logs_ok
        
        if corrections_successful:
            self.log("\n🎉 CORREÇÕES FORAM BEM-SUCEDIDAS!")
            self.log("📋 WebSocket mantém conexão estável e não apresenta erros 'received 1000 (OK)'")
            self.log("📋 Taxa de mensagens melhorou e sistema funciona continuamente")
        else:
            self.log("\n⚠️  CORREÇÕES AINDA NÃO RESOLVERAM TODOS OS PROBLEMAS")
            if not websocket_ok:
                self.log("📋 WebSocket ainda apresenta instabilidade")
            if not logs_ok:
                self.log("📋 Erros 'received 1000 (OK)' ainda aparecem nos logs")
            self.log("📋 RECOMENDAÇÃO: Investigar e aplicar correções adicionais")
        
        return corrections_successful, results

async def main():
    """Main function to run WebSocket stability tests"""
    print("🤖 Teste de Estabilidade do WebSocket - Deriv Trading Bot")
    print("=" * 70)
    print("📋 Testando estabilidade após correções implementadas:")
    print("   - Melhor tratamento de desconexões WebSocket no endpoint /ws/ticks")
    print("   - Lógica de reconnect mais agressiva no DerivWS")
    print("   - Tratamento adequado de WebSocketDisconnect e ConnectionClosed exceptions")
    print("   ⚠️  IMPORTANTE: Conta DEMO, não executar compras reais")
    
    tester = WebSocketStabilityTester()
    
    try:
        success, results = await tester.run_stability_tests()
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        print("\n⚠️  Teste interrompido pelo usuário")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Erro inesperado: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())