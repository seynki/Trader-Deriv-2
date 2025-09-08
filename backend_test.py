#!/usr/bin/env python3
"""
Backend API Testing for Deriv Trading Bot Strategy Runner Continuity
Tests as requested in Portuguese review:
🤖 TESTE DE CONTINUIDADE DO BOT TRADING - PRIORIDADE MÁXIMA

OBJETIVO: Validar que o Strategy Runner funciona infinitamente sem parar automaticamente

CONTEXTO CRÍTICO:
- Sistema tem Strategy Runner já implementado com Enhanced strategy loop
- Bot deve rodar infinitamente até usuário parar manualmente
- Problema reportado: "Bot para após um contrato"
- Sistema tem recuperação automática de erros

TESTES OBRIGATÓRIOS:
1. CONECTIVIDADE BÁSICA (confirmar sistema operacional):
   - GET /api/deriv/status (must return connected=true, authenticated=true)
   - GET /api/strategy/status (verificar estado inicial)

2. INICIAR ESTRATÉGIA:
   - POST /api/strategy/start com payload padrão

3. TESTE DE CONTINUIDADE (CRÍTICO):
   - Monitorar GET /api/strategy/status por 90 segundos
   - Verificar que running=true permanece true
   - Verificar que last_run_at continua atualizando (timestamp deve aumentar)
   - Documentar mudanças nos valores: today_pnl, today_trades, total_trades

4. ONLINE LEARNING ATIVO:
   - GET /api/ml/online/progress (verificar modelos ativos e updates)

CRITÉRIOS DE SUCESSO:
- ✅ running=true durante todo o teste (90s)
- ✅ last_run_at timestamp atualizando regularmente (a cada 10-20s)
- ✅ Sistema não para automaticamente
- ✅ Logs mostram estratégia rodando continuamente

CRITÉRIOS DE FALHA:
- ❌ running muda para false automaticamente
- ❌ last_run_at para de atualizar por >30s
- ❌ Estratégia para por conta própria

INSTRUÇÕES ESPECIAIS:
- NÃO usar /api/deriv/buy (manter paper mode apenas)
- Documentar todos os timestamps de last_run_at para provar continuidade
- Se estratégia parar automaticamente, isso é BUG CRÍTICO
- Relatar qualquer comportamento que indique parada automática

FOCO: O teste deve provar que o bot funciona INFINITAMENTE e nunca para sozinho.
"""

import requests
import json
import sys
import time
import asyncio
import websockets
from datetime import datetime

class DerivConnectivityTester:
    def __init__(self, base_url="https://finance-candle-ml.preview.emergentagent.com"):
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
        """Test WebSocket /api/ws/ticks - testar por 30 segundos para R_100,R_75,R_50 conforme review request"""
        self.log("\n" + "="*70)
        self.log("TEST: WEBSOCKET TICKS SPEED AND CONNECTIVITY")
        self.log("="*70)
        self.log("📋 Objetivo: Conectar ao WebSocket /api/ws/ticks por 30 segundos e medir velocidade")
        self.log("📋 Símbolos: R_100,R_75,R_50 (conforme review request)")
        self.log("📋 Taxa esperada: ~0.57 msg/s conforme usuário mencionou")
        self.log("📋 Verificar se conexão é estável (sem desconexões)")
        self.log("📋 Contar quantos ticks são recebidos")
        
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
            
            # Use websockets.connect without timeout parameter for compatibility
            websocket = await websockets.connect(ws_url)
            self.log("✅ WebSocket conectado com sucesso")
            
            try:
                # Send initial payload for R_100,R_75,R_50
                initial_payload = {"symbols": ["R_100", "R_75", "R_50"]}
                await websocket.send(json.dumps(initial_payload))
                self.log(f"📤 Payload inicial enviado: {initial_payload}")
                
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
                            
                            # Count different message types
                            if msg_type == 'tick':
                                tick_messages += 1
                                if symbol != 'unknown':
                                    symbols_detected.add(symbol)
                            elif msg_type == 'heartbeat':
                                heartbeat_messages += 1
                            
                            # Log every 50th message to avoid spam but show progress
                            if messages_received % 50 == 0 or messages_received <= 10:
                                elapsed = time.time() - start_time
                                rate = messages_received / elapsed if elapsed > 0 else 0
                                self.log(f"📊 Progresso: {messages_received} msgs ({tick_messages} ticks, {heartbeat_messages} heartbeats) em {elapsed:.1f}s - {rate:.2f} msg/s")
                                if symbol != 'unknown':
                                    self.log(f"   Último tick: {symbol} = {price}")
                            
                        except json.JSONDecodeError:
                            self.log(f"⚠️  Mensagem não-JSON recebida: {message[:100]}...")
                            
                    except asyncio.TimeoutError:
                        # No message received in 3 seconds - this might indicate instability
                        elapsed = time.time() - start_time
                        self.log(f"⚠️  Timeout aguardando mensagem (elapsed: {elapsed:.1f}s, timeouts: {connection_errors + 1})")
                        connection_errors += 1
                        
                        if connection_errors >= 10:
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
        
        # Determine if WebSocket is stable based on review requirements
        is_stable = True
        issues = []
        
        # Check if we received any messages
        if messages_received == 0:
            is_stable = False
            issues.append("Nenhuma mensagem recebida")
            
        # Check message rate (should be ~0.57 msg/s as per user feedback)
        elif message_rate < 0.4:  # Allow some tolerance
            is_stable = False
            issues.append(f"Taxa de mensagens muito baixa: {message_rate:.2f} msg/s (esperado ~0.57 msg/s)")
            
        # Check if we received ticks specifically
        if tick_messages == 0:
            is_stable = False
            issues.append("Nenhum tick recebido")
            
        # Check for excessive connection errors
        if connection_errors > 5:
            is_stable = False
            issues.append(f"Muitos timeouts/erros: {connection_errors}")
            
        # Check if we detected the expected symbols (R_100, R_75, R_50)
        expected_symbols = {"R_100", "R_75", "R_50"}
        if not symbols_detected.intersection(expected_symbols):
            is_stable = False
            issues.append(f"Nenhum dos símbolos esperados detectado: {expected_symbols}")
            
        # Check if test ran for sufficient time (at least 80% of expected duration)
        if elapsed_time < test_duration * 0.8:
            is_stable = False
            issues.append(f"Teste terminou prematuramente: {elapsed_time:.1f}s < {test_duration}s")
        
        # Check heartbeat functionality
        if heartbeat_messages == 0 and elapsed_time > 30:
            issues.append("Nenhum heartbeat recebido (esperado a cada 25s)")
        
        if is_stable:
            self.log("✅ WebSocket FUNCIONANDO CORRETAMENTE!")
            self.log(f"   ✓ Conexão mantida por {elapsed_time:.1f}s sem desconexões")
            self.log(f"   ✓ Taxa: {message_rate:.2f} msg/s (próximo ao esperado ~0.57 msg/s)")
            self.log(f"   ✓ Ticks recebidos: {tick_messages} de símbolos {list(symbols_detected)}")
            if heartbeat_messages > 0:
                self.log(f"   ✓ Heartbeats funcionando: {heartbeat_messages} recebidos")
            self.tests_passed += 1
        else:
            self.log("❌ WebSocket COM PROBLEMAS:")
            for issue in issues:
                self.log(f"   - {issue}")
        
        self.tests_run += 1
        
        return is_stable, {
            "elapsed_time": elapsed_time,
            "messages_received": messages_received,
            "tick_messages": tick_messages,
            "heartbeat_messages": heartbeat_messages,
            "message_rate": message_rate,
            "tick_rate": tick_rate,
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

    def test_strategy_start(self):
        """Test POST /api/strategy/start with default payload"""
        self.log("\n" + "="*70)
        self.log("TEST: INICIAR ESTRATÉGIA COM PAYLOAD PADRÃO")
        self.log("="*70)
        self.log("📋 Objetivo: POST /api/strategy/start com payload padrão para iniciar bot")
        
        # Default payload as specified in review request
        payload = {
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
        
        success, data, status_code = self.run_test(
            "Strategy Start",
            "POST",
            "strategy/start",
            200,
            payload
        )
        
        if not success:
            self.log(f"❌ CRITICAL: POST /api/strategy/start falhou - Status: {status_code}")
            return False, data
        
        self.log(f"✅ Estratégia iniciada com sucesso")
        return True, data

    def test_strategy_continuity(self, duration_seconds=90):
        """Test Strategy Runner continuity for specified duration (90s by default)"""
        self.log("\n" + "="*70)
        self.log("TEST CRÍTICO: CONTINUIDADE DO STRATEGY RUNNER (90 SEGUNDOS)")
        self.log("="*70)
        self.log("📋 Objetivo: Monitorar GET /api/strategy/status por 90 segundos")
        self.log("📋 Verificar que running=true permanece true")
        self.log("📋 Verificar que last_run_at continua atualizando")
        self.log("📋 Documentar mudanças nos valores: today_pnl, today_trades, total_trades")
        
        start_time = time.time()
        last_run_timestamps = []
        status_snapshots = []
        continuity_issues = []
        
        self.log(f"⏱️  Iniciando monitoramento por {duration_seconds} segundos...")
        
        check_count = 0
        while time.time() - start_time < duration_seconds:
            check_count += 1
            elapsed = time.time() - start_time
            
            # Get strategy status
            success, data, status_code = self.run_test(
                f"Strategy Status Check #{check_count}",
                "GET",
                "strategy/status",
                200,
                timeout=10
            )
            
            if not success:
                issue = f"Falha ao obter status após {elapsed:.1f}s (check #{check_count})"
                continuity_issues.append(issue)
                self.log(f"❌ {issue}")
                time.sleep(5)
                continue
            
            # Extract key metrics
            running = data.get('running', False)
            last_run_at = data.get('last_run_at')
            total_trades = data.get('total_trades', 0)
            today_pnl = data.get('daily_pnl', 0.0)
            wins = data.get('wins', 0)
            losses = data.get('losses', 0)
            
            # Record snapshot
            snapshot = {
                'elapsed': elapsed,
                'check': check_count,
                'running': running,
                'last_run_at': last_run_at,
                'total_trades': total_trades,
                'today_pnl': today_pnl,
                'wins': wins,
                'losses': losses,
                'timestamp': time.time()
            }
            status_snapshots.append(snapshot)
            
            # Track last_run_at timestamps
            if last_run_at:
                last_run_timestamps.append((elapsed, last_run_at))
            
            # Check for critical issues
            if not running:
                issue = f"CRÍTICO: running=false após {elapsed:.1f}s (check #{check_count})"
                continuity_issues.append(issue)
                self.log(f"❌ {issue}")
                break
            
            # Log progress every 15 seconds or on significant changes
            if check_count == 1 or elapsed % 15 < 5 or check_count % 6 == 0:
                self.log(f"📊 Check #{check_count} ({elapsed:.1f}s): running={running}, last_run_at={last_run_at}, trades={total_trades}, pnl={today_pnl:.2f}")
            
            # Wait 5 seconds between checks
            time.sleep(5)
        
        # Analysis
        total_elapsed = time.time() - start_time
        
        self.log(f"\n📊 ANÁLISE DE CONTINUIDADE ({total_elapsed:.1f}s, {check_count} checks):")
        
        # Check if strategy stayed running
        running_checks = [s for s in status_snapshots if s['running']]
        stopped_checks = [s for s in status_snapshots if not s['running']]
        
        self.log(f"   Checks com running=true: {len(running_checks)}/{len(status_snapshots)}")
        self.log(f"   Checks com running=false: {len(stopped_checks)}")
        
        # Check last_run_at updates
        if len(last_run_timestamps) >= 2:
            first_timestamp = last_run_timestamps[0][1]
            last_timestamp = last_run_timestamps[-1][1]
            timestamp_updates = len(set(ts[1] for ts in last_run_timestamps))
            
            self.log(f"   last_run_at updates: {timestamp_updates} diferentes timestamps")
            self.log(f"   Primeiro timestamp: {first_timestamp}")
            self.log(f"   Último timestamp: {last_timestamp}")
            
            # Check if timestamps are updating regularly
            if timestamp_updates < 3 and total_elapsed > 30:
                issue = f"last_run_at não está atualizando regularmente ({timestamp_updates} updates em {total_elapsed:.1f}s)"
                continuity_issues.append(issue)
        else:
            issue = "Poucos timestamps de last_run_at capturados"
            continuity_issues.append(issue)
        
        # Check for trade activity changes
        if len(status_snapshots) >= 2:
            first_snapshot = status_snapshots[0]
            last_snapshot = status_snapshots[-1]
            
            trade_change = last_snapshot['total_trades'] - first_snapshot['total_trades']
            pnl_change = last_snapshot['today_pnl'] - first_snapshot['today_pnl']
            
            self.log(f"   Mudança em trades: {trade_change}")
            self.log(f"   Mudança em PnL: {pnl_change:.2f}")
        
        # Determine success
        is_continuous = len(continuity_issues) == 0 and len(running_checks) == len(status_snapshots)
        
        if is_continuous:
            self.log("✅ CONTINUIDADE CONFIRMADA!")
            self.log("   ✓ running=true durante todo o teste")
            self.log("   ✓ last_run_at atualizando regularmente")
            self.log("   ✓ Sistema não parou automaticamente")
            self.tests_passed += 1
        else:
            self.log("❌ PROBLEMAS DE CONTINUIDADE DETECTADOS:")
            for issue in continuity_issues:
                self.log(f"   - {issue}")
        
        self.tests_run += 1
        
        return is_continuous, {
            'total_elapsed': total_elapsed,
            'checks_performed': check_count,
            'running_checks': len(running_checks),
            'stopped_checks': len(stopped_checks),
            'timestamp_updates': len(set(ts[1] for ts in last_run_timestamps)) if last_run_timestamps else 0,
            'continuity_issues': continuity_issues,
            'status_snapshots': status_snapshots[-5:],  # Last 5 snapshots
            'is_continuous': is_continuous
        }

    async def run_review_request_tests(self):
        """Run Strategy Runner continuity tests as requested in Portuguese review"""
        self.log("\n" + "🚀" + "="*68)
        self.log("TESTE DE CONTINUIDADE DO BOT TRADING - PRIORIDADE MÁXIMA")
        self.log("🚀" + "="*68)
        self.log("📋 Conforme solicitado na review request:")
        self.log("   1. CONECTIVIDADE BÁSICA - GET /api/deriv/status (connected=true, authenticated=true)")
        self.log("   2. ESTADO INICIAL - GET /api/strategy/status (verificar estado inicial)")
        self.log("   3. INICIAR ESTRATÉGIA - POST /api/strategy/start com payload padrão")
        self.log("   4. TESTE DE CONTINUIDADE - Monitorar por 90 segundos:")
        self.log("      - Verificar que running=true permanece true")
        self.log("      - Verificar que last_run_at continua atualizando")
        self.log("      - Documentar mudanças nos valores")
        self.log("   5. ONLINE LEARNING ATIVO - GET /api/ml/online/progress")
        self.log("   🎯 FOCO: Provar que o bot funciona INFINITAMENTE e nunca para sozinho")
        self.log(f"   🌐 Base URL: {self.base_url}")
        
        results = {}
        
        # Test 1: Deriv Status - conectividade básica
        self.log("\n🔍 TESTE 1: CONECTIVIDADE BÁSICA")
        deriv_ok, deriv_data = self.test_deriv_status()
        results['deriv_status'] = deriv_ok
        
        if not deriv_ok:
            self.log("❌ CRITICAL: Deriv não conectado - não é possível testar Strategy Runner")
            return False, results
        
        # Verify connected=true and authenticated=true
        connected = deriv_data.get('connected', False) if isinstance(deriv_data, dict) else False
        authenticated = deriv_data.get('authenticated', False) if isinstance(deriv_data, dict) else False
        
        if not connected or not authenticated:
            self.log(f"❌ CRITICAL: Deriv status inadequado - connected={connected}, authenticated={authenticated}")
            return False, results
        
        # Test 2: Strategy Status - estado inicial
        self.log("\n🔍 TESTE 2: ESTADO INICIAL DA ESTRATÉGIA")
        initial_status_ok, initial_status_data = self.test_strategy_status()
        results['initial_strategy_status'] = initial_status_ok
        
        if not initial_status_ok:
            self.log("❌ CRITICAL: Não foi possível obter status inicial da estratégia")
            return False, results
        
        # Test 3: Start Strategy - iniciar estratégia
        self.log("\n🔍 TESTE 3: INICIAR ESTRATÉGIA")
        start_ok, start_data = self.test_strategy_start()
        results['strategy_start'] = start_ok
        
        if not start_ok:
            self.log("❌ CRITICAL: Falha ao iniciar estratégia")
            return False, results
        
        # Test 4: Continuity Test - TESTE PRINCIPAL (90 segundos)
        self.log("\n🔍 TESTE 4: CONTINUIDADE DO STRATEGY RUNNER (90 SEGUNDOS)")
        continuity_ok, continuity_data = self.test_strategy_continuity(90)
        results['strategy_continuity'] = continuity_ok
        
        # Test 5: Online Learning Progress
        self.log("\n🔍 TESTE 5: ONLINE LEARNING ATIVO")
        online_learning_ok, online_learning_data = self.test_online_learning_progress()
        results['online_learning'] = online_learning_ok
        
        # Final Summary
        self.log("\n" + "🏁" + "="*68)
        self.log("RESULTADO FINAL: Teste de Continuidade do Bot Trading")
        self.log("🏁" + "="*68)
        
        # Test 1 Results
        if deriv_ok and connected and authenticated:
            self.log(f"✅ 1. CONECTIVIDADE BÁSICA: connected=true, authenticated=true ✓")
        else:
            self.log(f"❌ 1. CONECTIVIDADE BÁSICA: FAILED")
        
        # Test 2 Results
        if initial_status_ok:
            running = initial_status_data.get('running', False) if isinstance(initial_status_data, dict) else False
            total_trades = initial_status_data.get('total_trades', 0) if isinstance(initial_status_data, dict) else 0
            self.log(f"✅ 2. ESTADO INICIAL: running={running}, total_trades={total_trades} ✓")
        else:
            self.log("❌ 2. ESTADO INICIAL: FAILED")
        
        # Test 3 Results
        if start_ok:
            self.log("✅ 3. INICIAR ESTRATÉGIA: Estratégia iniciada com sucesso ✓")
        else:
            self.log("❌ 3. INICIAR ESTRATÉGIA: FAILED")
        
        # Test 4 Results - CRÍTICO
        if continuity_ok:
            elapsed = continuity_data.get('total_elapsed', 0) if isinstance(continuity_data, dict) else 0
            checks = continuity_data.get('checks_performed', 0) if isinstance(continuity_data, dict) else 0
            running_checks = continuity_data.get('running_checks', 0) if isinstance(continuity_data, dict) else 0
            timestamp_updates = continuity_data.get('timestamp_updates', 0) if isinstance(continuity_data, dict) else 0
            
            self.log(f"✅ 4. CONTINUIDADE: FUNCIONANDO por {elapsed:.1f}s ✓")
            self.log(f"   📊 {running_checks}/{checks} checks com running=true")
            self.log(f"   📈 {timestamp_updates} atualizações de last_run_at")
            self.log(f"   🎯 Sistema NÃO parou automaticamente")
        else:
            elapsed = continuity_data.get('total_elapsed', 0) if isinstance(continuity_data, dict) else 0
            issues = continuity_data.get('continuity_issues', []) if isinstance(continuity_data, dict) else []
            
            self.log(f"❌ 4. CONTINUIDADE: PROBLEMAS após {elapsed:.1f}s")
            self.log(f"   🚨 Problemas detectados: {len(issues)}")
            for issue in issues[:3]:  # Show first 3 issues
                self.log(f"      - {issue}")
        
        # Test 5 Results
        if online_learning_ok:
            active_models = online_learning_data.get('active_models', 0) if isinstance(online_learning_data, dict) else 0
            total_updates = online_learning_data.get('total_updates', 0) if isinstance(online_learning_data, dict) else 0
            self.log(f"✅ 5. ONLINE LEARNING: {active_models} modelo(s) ativo(s), {total_updates} update(s) ✓")
        else:
            self.log("❌ 5. ONLINE LEARNING: FAILED")
        
        # Overall assessment based on review requirements
        basic_connectivity = deriv_ok and connected and authenticated
        strategy_working = initial_status_ok and start_ok
        continuity_proven = continuity_ok
        online_learning_working = online_learning_ok
        
        if basic_connectivity and strategy_working and continuity_proven and online_learning_working:
            self.log("\n🎉 TODOS OS TESTES CRÍTICOS PASSARAM!")
            self.log("📋 Validações bem-sucedidas:")
            self.log("   ✅ Deriv conectado e autenticado")
            self.log("   ✅ Estratégia inicia corretamente")
            self.log("   ✅ Bot funciona INFINITAMENTE (90s+ sem parar)")
            self.log("   ✅ Sistema de retreinamento automático ativo")
            self.log("   🎯 CONCLUSÃO: Bot NÃO para após um contrato - problema RESOLVIDO!")
        elif basic_connectivity and strategy_working and continuity_proven:
            self.log("\n🎯 CONTINUIDADE CONFIRMADA, MAS VERIFICAR ONLINE LEARNING")
            self.log("   ✅ Bot funciona infinitamente sem parar")
            if not online_learning_working:
                self.log("   ⚠️  Sistema de retreinamento automático com problemas")
        else:
            self.log("\n❌ PROBLEMAS CRÍTICOS DETECTADOS")
            if not basic_connectivity:
                self.log("   ❌ Deriv não conectado adequadamente")
            if not strategy_working:
                self.log("   ❌ Estratégia não inicia ou não funciona")
            if not continuity_proven:
                self.log("   ❌ BOT PARA AUTOMATICAMENTE - BUG CRÍTICO CONFIRMADO")
                self.log("   📋 FOCO: Bot para após um contrato - problema PERSISTE")
        
        return basic_connectivity and strategy_working and continuity_proven, results

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
    """Main function to run Strategy Runner continuity tests"""
    print("🚀 TESTE DE CONTINUIDADE DO BOT TRADING - PRIORIDADE MÁXIMA")
    print("=" * 70)
    print("📋 Conforme solicitado na review request:")
    print("   OBJETIVO: Validar que o Strategy Runner funciona infinitamente sem parar automaticamente")
    print("   PROBLEMA: Bot para após um contrato")
    print("   TESTES:")
    print("   1. GET /api/deriv/status (connected=true, authenticated=true)")
    print("   2. GET /api/strategy/status (verificar estado inicial)")
    print("   3. POST /api/strategy/start com payload padrão")
    print("   4. Monitorar GET /api/strategy/status por 90 segundos:")
    print("      - Verificar que running=true permanece true")
    print("      - Verificar que last_run_at continua atualizando")
    print("   5. GET /api/ml/online/progress (verificar modelos ativos)")
    print("   🎯 FOCO: Provar que o bot funciona INFINITAMENTE e nunca para sozinho")
    
    # Use the URL from frontend/.env as specified
    tester = DerivConnectivityTester()
    
    try:
        # Run review request tests
        success, results = await tester.run_review_request_tests()
        
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