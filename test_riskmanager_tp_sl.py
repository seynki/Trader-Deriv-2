#!/usr/bin/env python3
"""
RiskManager Take Profit / Stop Loss Validation Test
Tests the updated RiskManager behavior as per Portuguese review request:

1) GET /api/deriv/status → connected=true, authenticated=true
2) POST /api/deriv/buy com body:
   {symbol:'R_10', type:'CALLPUT', contract_type:'CALL', duration:5, duration_unit:'t', 
    stake:1.0, currency:'USD', take_profit_usd:0.05, stop_loss_usd:null}
3) Abrir WebSocket /api/ws/contract/{contract_id} por até 60s
   - Confirmar que NÃO há venda quando profit estiver negativo (ex.: -0.05)
   - Confirmar venda imediata assim que profit >= +0.05
4) Opcional: Testar SL separado
"""

import requests
import json
import time
from datetime import datetime
import websocket
import threading

def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

def test_riskmanager_tp_sl_validation():
    """Execute the RiskManager TP/SL validation test"""
    
    base_url = "https://strategy-validator-2.preview.emergentagent.com"
    api_url = f"{base_url}/api"
    session = requests.Session()
    session.headers.update({'Content-Type': 'application/json'})
    
    log("\n" + "🛡️" + "="*68)
    log("TESTE RISKMANAGER: TP/SL VALIDATION - UPDATED BEHAVIOR")
    log("🛡️" + "="*68)
    
    test_results = {
        "deriv_connectivity": False,
        "tp_only_contract_created": False,
        "tp_only_websocket_monitoring": False,
        "tp_only_no_sell_at_negative": False,
        "tp_only_sell_at_tp": False,
        "sl_only_contract_created": False,
        "sl_only_sell_at_sl": False
    }
    
    json_responses = {}
    
    try:
        # Test 1: GET /api/deriv/status
        log("\n🔍 TEST 1: GET /api/deriv/status")
        log("   Verificar connected=true, authenticated=true")
        
        # Try multiple times with increasing timeout
        for attempt in range(3):
            try:
                timeout = 15 + (attempt * 5)  # 15s, 20s, 25s
                log(f"   Tentativa {attempt + 1}/3 (timeout: {timeout}s)")
                
                response = session.get(f"{api_url}/deriv/status", timeout=timeout)
                log(f"   GET /api/deriv/status: {response.status_code}")
                
                if response.status_code == 200:
                    status_data = response.json()
                    json_responses["deriv_status"] = status_data
                    log(f"   Response: {json.dumps(status_data, indent=2)}")
                    
                    connected = status_data.get('connected')
                    authenticated = status_data.get('authenticated')
                    environment = status_data.get('environment')
                    
                    log(f"   📊 Deriv API Status:")
                    log(f"      Connected: {connected}")
                    log(f"      Authenticated: {authenticated}")
                    log(f"      Environment: {environment}")
                    
                    if connected == True and authenticated == True:
                        test_results["deriv_connectivity"] = True
                        log("✅ Test 1 OK: Deriv API conectada e autenticada")
                        break
                    else:
                        log(f"   ⏳ Aguardando conexão... (connected={connected}, auth={authenticated})")
                        if attempt < 2:
                            time.sleep(2)
                else:
                    log(f"   ❌ HTTP {response.status_code}")
                    if attempt < 2:
                        time.sleep(2)
                        
            except Exception as e:
                log(f"   ⚠️  Attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    time.sleep(2)
        
        if not test_results["deriv_connectivity"]:
            log("❌ Test 1 FALHOU: Deriv API não conectou após 3 tentativas")
            return False, test_results, json_responses
        
        # Test 2: POST /api/deriv/buy - TP-only scenario
        log("\n🔍 TEST 2: POST /api/deriv/buy - TP-only scenario")
        log("   Body: {symbol:'R_10', type:'CALLPUT', contract_type:'CALL', duration:5, duration_unit:'t',")
        log("         stake:1.0, currency:'USD', take_profit_usd:0.05, stop_loss_usd:null}")
        
        tp_only_payload = {
            "symbol": "R_10",
            "type": "CALLPUT", 
            "contract_type": "CALL",
            "duration": 5,
            "duration_unit": "t",
            "stake": 1.0,
            "currency": "USD",
            "take_profit_usd": 0.05,
            "stop_loss_usd": None
        }
        
        log(f"   Payload: {json.dumps(tp_only_payload, indent=2)}")
        
        response = session.post(f"{api_url}/deriv/buy", json=tp_only_payload, timeout=30)
        log(f"   POST /api/deriv/buy (TP-only): {response.status_code}")
        
        tp_contract_id = None
        if response.status_code == 200:
            buy_data = response.json()
            json_responses["deriv_buy_tp_only"] = buy_data
            log(f"   Response: {json.dumps(buy_data, indent=2)}")
            
            tp_contract_id = buy_data.get('contract_id')
            buy_price = buy_data.get('buy_price')
            payout = buy_data.get('payout')
            transaction_id = buy_data.get('transaction_id')
            
            log(f"   📊 Contract Created (TP-only):")
            log(f"      Contract ID: {tp_contract_id}")
            log(f"      Buy Price: {buy_price}")
            log(f"      Payout: {payout}")
            log(f"      Transaction ID: {transaction_id}")
            log(f"      Take Profit: 0.05 USD")
            log(f"      Stop Loss: null")
            
            if tp_contract_id is not None:
                test_results["tp_only_contract_created"] = True
                log("✅ Test 2 OK: Contrato TP-only criado")
                log(f"   🎯 Contract ID: {tp_contract_id}")
            else:
                log("❌ Test 2 FALHOU: Contract ID não retornado")
                return False, test_results, json_responses
        else:
            log(f"❌ Test 2 FALHOU - HTTP {response.status_code}")
            try:
                error_data = response.json()
                json_responses["deriv_buy_tp_only"] = error_data
                log(f"   Error: {error_data}")
            except:
                log(f"   Error text: {response.text}")
            return False, test_results, json_responses
        
        # Test 3: Monitor TP-only contract via WebSocket
        log("\n🔍 TEST 3: Monitor TP-only contract via WebSocket")
        log("   Abrir WebSocket /api/ws/contract/{contract_id} por até 60s")
        log("   Verificar:")
        log("   - NÃO há venda quando profit estiver negativo (ex.: -0.05)")
        log("   - Venda imediata assim que profit >= +0.05")
        
        monitoring_result = monitor_contract_websocket(tp_contract_id, 60, log)
        
        if monitoring_result["connection_established"]:
            test_results["tp_only_websocket_monitoring"] = True
            log("✅ Test 3 OK: WebSocket monitoring funcionando")
            
            # Validate TP-only behavior
            if monitoring_result["no_sell_at_negative"]:
                test_results["tp_only_no_sell_at_negative"] = True
                log("✅ TP-ONLY RULE 1: NÃO vendeu com profit negativo")
            else:
                log("❌ TP-ONLY RULE 1 VIOLADA: Vendeu com profit negativo!")
            
            if monitoring_result["sell_at_tp"]:
                test_results["tp_only_sell_at_tp"] = True
                log("✅ TP-ONLY RULE 2: Vendeu quando profit >= 0.05")
            else:
                log("ℹ️  TP-ONLY RULE 2: TP não foi atingido durante monitoramento")
                log(f"   Max profit observado: {monitoring_result['max_profit']:.4f}")
        else:
            log("❌ Test 3 FALHOU: WebSocket não conectou")
        
        json_responses["tp_only_monitoring"] = monitoring_result
        
        # Test 4: Optional SL-only scenario
        log("\n🔍 TEST 4: POST /api/deriv/buy - SL-only scenario (opcional)")
        log("   Body: {symbol:'R_10', type:'CALLPUT', contract_type:'PUT', duration:5, duration_unit:'t',")
        log("         stake:1.0, currency:'USD', stop_loss_usd:0.05, take_profit_usd:null}")
        
        sl_only_payload = {
            "symbol": "R_10",
            "type": "CALLPUT", 
            "contract_type": "PUT",
            "duration": 5,
            "duration_unit": "t",
            "stake": 1.0,
            "currency": "USD",
            "stop_loss_usd": 0.05,
            "take_profit_usd": None
        }
        
        log(f"   Payload: {json.dumps(sl_only_payload, indent=2)}")
        
        response = session.post(f"{api_url}/deriv/buy", json=sl_only_payload, timeout=30)
        log(f"   POST /api/deriv/buy (SL-only): {response.status_code}")
        
        sl_contract_id = None
        if response.status_code == 200:
            buy_data = response.json()
            json_responses["deriv_buy_sl_only"] = buy_data
            log(f"   Response: {json.dumps(buy_data, indent=2)}")
            
            sl_contract_id = buy_data.get('contract_id')
            if sl_contract_id is not None:
                test_results["sl_only_contract_created"] = True
                log("✅ Test 4 OK: Contrato SL-only criado")
                log(f"   🎯 Contract ID: {sl_contract_id}")
                
                # Monitor SL-only contract briefly
                log("   Monitorando SL-only por 30s...")
                sl_monitoring_result = monitor_contract_websocket(sl_contract_id, 30, log)
                
                if sl_monitoring_result["sell_at_sl"]:
                    test_results["sl_only_sell_at_sl"] = True
                    log("✅ SL-ONLY RULE: Vendeu quando profit <= -0.05")
                else:
                    log("ℹ️  SL-ONLY RULE: SL não foi atingido durante monitoramento")
                
                json_responses["sl_only_monitoring"] = sl_monitoring_result
            else:
                log("❌ Test 4 FALHOU: Contract ID não retornado")
        else:
            log(f"ℹ️  Test 4 OPCIONAL - HTTP {response.status_code} (não crítico)")
            try:
                error_data = response.json()
                json_responses["deriv_buy_sl_only"] = error_data
                log(f"   Error: {error_data}")
            except:
                log(f"   Error text: {response.text}")
        
        # Final analysis
        log("\n" + "🏁" + "="*68)
        log("RESULTADO FINAL: RiskManager TP/SL Validation")
        log("🏁" + "="*68)
        
        passed_tests = sum(test_results.values())
        total_tests = len(test_results)
        success_rate = (passed_tests / total_tests) * 100
        
        log(f"📊 ESTATÍSTICAS:")
        log(f"   Testes executados: {total_tests}")
        log(f"   Testes bem-sucedidos: {passed_tests}")
        log(f"   Taxa de sucesso: {success_rate:.1f}%")
        
        log(f"\n📋 DETALHES POR TESTE:")
        test_names = {
            "deriv_connectivity": "1) Conectividade - GET /api/deriv/status",
            "tp_only_contract_created": "2) Contrato TP-only - POST /api/deriv/buy",
            "tp_only_websocket_monitoring": "3) WebSocket monitoring - /api/ws/contract/{id}",
            "tp_only_no_sell_at_negative": "4) CRÍTICO: NÃO vender com profit negativo",
            "tp_only_sell_at_tp": "5) TP trigger - vender quando profit >= 0.05",
            "sl_only_contract_created": "6) Contrato SL-only (opcional)",
            "sl_only_sell_at_sl": "7) SL trigger - vender quando profit <= -0.05"
        }
        
        for test_key, passed in test_results.items():
            test_name = test_names.get(test_key, test_key)
            status = "✅ SUCESSO" if passed else "❌ FALHOU"
            log(f"   {test_name}: {status}")
        
        # Critical validation
        tp_critical_success = (test_results.get("deriv_connectivity") and 
                              test_results.get("tp_only_contract_created") and
                              test_results.get("tp_only_websocket_monitoring") and
                              test_results.get("tp_only_no_sell_at_negative"))
        
        log(f"\n🔍 VALIDAÇÃO CRÍTICA - RISKMANAGER TP/SL SEPARATION:")
        if tp_critical_success:
            log("✅ APROVADO: RiskManager comportamento TP-only funcionando corretamente")
            log("   - Contrato criado com TP=0.05, SL=null")
            log("   - NÃO vende com profit negativo")
            if test_results.get("tp_only_sell_at_tp"):
                log("   - Vende quando profit >= 0.05")
            else:
                log("   - TP não foi atingido durante teste (condições de mercado)")
        else:
            log("❌ REPROVADO: Problemas detectados no comportamento TP-only")
        
        if test_results.get("sl_only_contract_created"):
            log("✅ SL-ONLY BEHAVIOR: Testado")
            if test_results.get("sl_only_sell_at_sl"):
                log("   - Vende quando profit <= -0.05 (sem TP ativo)")
            else:
                log("   - SL não foi atingido durante teste")
        else:
            log("ℹ️  SL-ONLY BEHAVIOR: Não testado (opcional)")
        
        # Report contract IDs tested
        if tp_contract_id:
            log(f"\n📋 Contract ID TP-only testado: {tp_contract_id}")
        if sl_contract_id:
            log(f"📋 Contract ID SL-only testado: {sl_contract_id}")
        
        # Report all JSON responses
        log(f"\n📄 TODOS OS JSONs RETORNADOS:")
        log("="*50)
        for step_name, json_data in json_responses.items():
            log(f"\n🔹 {step_name.upper()}:")
            log(json.dumps(json_data, indent=2, ensure_ascii=False))
            log("-" * 30)
        
        return tp_critical_success, test_results, json_responses
        
    except Exception as e:
        log(f"❌ ERRO CRÍTICO NO TESTE: {e}")
        import traceback
        log(f"   Traceback: {traceback.format_exc()}")
        
        return False, {
            "error": "critical_test_exception",
            "details": str(e),
            "test_results": test_results
        }, {}


def monitor_contract_websocket(contract_id, duration, log_func):
    """Monitor a contract via WebSocket and track TP/SL behavior"""
    
    try:
        ws_url = f"wss://auto-trading-check.preview.emergentagent.com/api/ws/contract/{contract_id}"
        log_func(f"   📡 Conectando WebSocket: {ws_url}")
        
        result = {
            "connection_established": False,
            "messages_received": [],
            "no_sell_at_negative": True,  # Assume true until proven false
            "sell_at_tp": False,
            "sell_at_sl": False,
            "max_profit": 0.0,
            "min_profit": 0.0,
            "profit_timeline": [],
            "negative_profit_periods": [],
            "sell_attempts_during_negative": []
        }
        
        monitoring_start_time = time.time()
        
        def on_message(ws, message):
            try:
                data = json.loads(message)
                result["messages_received"].append(data)
                
                profit = data.get('profit', 0)
                status = data.get('status', 'unknown')
                is_expired = data.get('is_expired', False)
                
                if profit is not None:
                    profit_float = float(profit)
                    elapsed = time.time() - monitoring_start_time
                    
                    result["max_profit"] = max(result["max_profit"], profit_float)
                    result["min_profit"] = min(result["min_profit"], profit_float)
                    
                    profit_entry = {
                        "time": elapsed,
                        "profit": profit_float,
                        "status": status,
                        "is_expired": is_expired
                    }
                    result["profit_timeline"].append(profit_entry)
                    
                    log_func(f"   📨 t={elapsed:.1f}s: profit={profit_float:.4f}, status={status}, expired={is_expired}")
                    
                    # Track negative profit periods
                    if profit_float < 0:
                        negative_period = {
                            "time": elapsed,
                            "profit": profit_float,
                            "status": status
                        }
                        result["negative_profit_periods"].append(negative_period)
                        
                        log_func(f"   ⚠️  PROFIT NEGATIVO: {profit_float:.4f}")
                        
                        # Check if there's a sell attempt during negative profit
                        if status == 'sold' or is_expired:
                            sell_attempt = {
                                "time": elapsed,
                                "profit_at_sell": profit_float,
                                "status": status,
                                "is_expired": is_expired,
                                "violation": True
                            }
                            result["sell_attempts_during_negative"].append(sell_attempt)
                            result["no_sell_at_negative"] = False
                            log_func(f"   🚨 VIOLAÇÃO: Tentativa de venda com profit negativo {profit_float:.4f}!")
                    
                    # Check if TP threshold reached (>= 0.05)
                    if profit_float >= 0.05:
                        log_func(f"   🎯 TP THRESHOLD ATINGIDO! profit={profit_float:.4f} >= 0.05")
                        
                        if status == 'sold' or is_expired:
                            result["sell_at_tp"] = True
                            log_func(f"   ✅ VENDA APÓS TP: status={status}, expired={is_expired}")
                    
                    # Check if SL threshold reached (<= -0.05)
                    if profit_float <= -0.05:
                        log_func(f"   🛑 SL THRESHOLD ATINGIDO! profit={profit_float:.4f} <= -0.05")
                        
                        if status == 'sold' or is_expired:
                            result["sell_at_sl"] = True
                            log_func(f"   ✅ VENDA APÓS SL: status={status}, expired={is_expired}")
                
            except Exception as e:
                log_func(f"   ⚠️  Error parsing WebSocket message: {e}")
        
        def on_open(ws):
            result["connection_established"] = True
            log_func("   ✅ WebSocket connection established")
        
        def on_error(ws, error):
            log_func(f"   ❌ WebSocket error: {error}")
        
        def on_close(ws, close_status_code, close_msg):
            log_func(f"   🔌 WebSocket closed: {close_status_code}")
        
        # Create WebSocket connection
        ws = websocket.WebSocketApp(ws_url,
                                  on_open=on_open,
                                  on_message=on_message,
                                  on_error=on_error,
                                  on_close=on_close)
        
        # Run WebSocket in a separate thread
        ws_thread = threading.Thread(target=ws.run_forever)
        ws_thread.daemon = True
        ws_thread.start()
        
        # Monitor for specified duration
        log_func(f"   ⏱️  Monitorando por {duration} segundos...")
        
        for second in range(duration):
            time.sleep(1)
            elapsed = second + 1
            
            if elapsed % 15 == 0:  # Log every 15 seconds
                log_func(f"   📊 Status (t={elapsed}s): messages={len(result['messages_received'])}, "
                        f"profit_range=[{result['min_profit']:.4f}, {result['max_profit']:.4f}]")
            
            # Early exit if contract expired
            if len(result["messages_received"]) > 0:
                latest_msg = result["messages_received"][-1]
                if latest_msg.get('is_expired', False) and elapsed >= 20:
                    log_func(f"   ⏰ Contrato expirou em {elapsed}s - finalizando monitoramento")
                    break
        
        # Close WebSocket
        ws.close()
        
        # Final validation
        if len(result["sell_attempts_during_negative"]) == 0:
            log_func("✅ VALIDAÇÃO: NÃO houve tentativas de venda com profit negativo")
        else:
            log_func(f"❌ VIOLAÇÃO: {len(result['sell_attempts_during_negative'])} tentativas de venda com profit negativo!")
        
        return result
        
    except Exception as e:
        log_func(f"   ❌ WebSocket monitoring failed: {e}")
        return {"error": str(e), "connection_established": False}


if __name__ == "__main__":
    success, results, responses = test_riskmanager_tp_sl_validation()
    
    if success:
        print("\n✅ TESTE CONCLUÍDO COM SUCESSO!")
        print("🛡️ RiskManager TP/SL separation funcionando corretamente")
    else:
        print("\n❌ TESTE FALHOU!")
        print("⚠️  Verificar implementação do RiskManager")
        if not results.get("tp_only_no_sell_at_negative", True):
            print("🚨 Possível violação: venda durante profit negativo")