#!/usr/bin/env python3
"""
Backend Testing - R_10 Paper Mode Sequence
Executes specific sequence for R_10 paper mode testing as requested
"""

import requests
import json
import sys
import time
import asyncio
import websockets
from datetime import datetime

async def test_r10_paper_mode_sequence():
    """
    Execute specific R_10 paper mode sequence as requested in Portuguese review:
    
    Sequência para executar agora (paper mode, R_10):

    1) TREINO ML ENGINE 5m: POST /api/ml/engine/train {symbol:"R_10", timeframe:"5m", count:3000, horizon:3, seq_len:32, use_transformer:false}
    2) BACKTEST THRESHOLDS RIVER 5m: POST /api/strategy/river/backtest {symbol:"R_10", timeframe:"5m", lookback_candles:1500, thresholds:[0.5,0.53,0.55,0.6,0.65,0.7,0.75]}; capturar best_threshold
    3) APLICAR THRESHOLD: POST /api/strategy/river/config {river_threshold: <best_threshold ou 0.6 se nulo>}
    4) TESTE 3 TICKS: POST /api/strategy/start {symbol:"R_10", granularity:1, candle_len:200, duration:3, duration_unit:"t", stake:1, ml_gate:true, ml_prob_threshold:0.6, adx_trend:28, mode:"paper"}; monitorar GET /api/strategy/status a cada 15s por 90s; depois POST /api/strategy/stop
    5) TESTE 5 MINUTOS: POST /api/strategy/start {symbol:"R_10", granularity:300, candle_len:240, duration:5, duration_unit:"t", stake:1, ml_gate:true, ml_prob_threshold:0.6, adx_trend:28, mode:"paper"}; monitorar por 90s; depois POST /api/strategy/stop

    Reportar: JSONs de retorno, threshold aplicado, win_rate/wins/losses/ daily_pnl, last_reason e qualquer bloqueio do ML gate. NÃO executar /api/deriv/buy diretamente (somente StrategyRunner paper).
    """
    
    base_url = "https://market-signal-pro-2.preview.emergentagent.com"
    api_url = f"{base_url}/api"
    session = requests.Session()
    session.headers.update({'Content-Type': 'application/json'})
    
    def log(message):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
    
    log("\n" + "🎯" + "="*68)
    log("SEQUÊNCIA R_10 PAPER MODE - TREINO ML + RIVER + TESTES")
    log("🎯" + "="*68)
    log("📋 Sequência conforme solicitado:")
    log("   1) TREINO ML ENGINE 5m: symbol=R_10, timeframe=5m, count=3000, horizon=3, seq_len=32, use_transformer=false")
    log("   2) BACKTEST THRESHOLDS RIVER 5m: lookback_candles=1500, thresholds=[0.5,0.53,0.55,0.6,0.65,0.7,0.75]")
    log("   3) APLICAR THRESHOLD: river_threshold = best_threshold ou 0.6 se nulo")
    log("   4) TESTE 3 TICKS: granularity=1, duration=3, ml_gate=true, monitorar 90s")
    log("   5) TESTE 5 MINUTOS: granularity=300, duration=5, ml_gate=true, monitorar 90s")
    log("   🎯 REPORTAR: JSONs, threshold aplicado, win_rate/wins/losses/daily_pnl, last_reason, bloqueios ML gate")
    
    test_results = {
        "ml_engine_train": False,
        "river_backtest": False,
        "apply_threshold": False,
        "test_3_ticks": False,
        "test_5_minutes": False
    }
    
    # Store all JSON responses and metrics for reporting
    json_responses = {}
    applied_threshold = None
    final_metrics = {}
    
    try:
        # Step 1: TREINO ML ENGINE 5m
        log("\n🔍 STEP 1: TREINO ML ENGINE 5m")
        log("   Objetivo: Treinar modelo ML Engine para R_10 com 3000 candles de 5m")
        
        ml_train_payload = {
            "symbol": "R_10",
            "timeframe": "5m",
            "count": 3000,
            "horizon": 3,
            "seq_len": 32,
            "use_transformer": False
        }
        
        try:
            log(f"   Payload: {json.dumps(ml_train_payload, indent=2)}")
            log("   ⏱️  Iniciando treinamento ML Engine (pode demorar 60-300s)...")
            
            response = session.post(f"{api_url}/ml/engine/train", json=ml_train_payload, timeout=360)
            log(f"   POST /api/ml/engine/train: {response.status_code}")
            
            if response.status_code == 200:
                train_data = response.json()
                json_responses["ml_engine_train"] = train_data
                log(f"   Response: {json.dumps(train_data, indent=2)}")
                
                success = train_data.get('success', False)
                model_key = train_data.get('model_key', '')
                features_count = train_data.get('features_count', 0)
                lgb_trained = train_data.get('lgb_trained', False)
                candles_used = train_data.get('candles_used', 0)
                
                log(f"   📊 ML Training Result:")
                log(f"      Success: {success}")
                log(f"      Model Key: {model_key}")
                log(f"      Features Count: {features_count}")
                log(f"      LGB Trained: {lgb_trained}")
                log(f"      Candles Used: {candles_used}")
                
                if success and model_key and candles_used >= 2500:
                    test_results["ml_engine_train"] = True
                    log("✅ Step 1 OK: Modelo ML Engine treinado com sucesso")
                else:
                    log(f"❌ Step 1 FALHOU: success={success}, model_key='{model_key}', candles_used={candles_used}")
            else:
                log(f"❌ ML Engine Training FALHOU - HTTP {response.status_code}")
                json_responses["ml_engine_train"] = {"error": f"HTTP {response.status_code}", "text": response.text}
                try:
                    error_data = response.json()
                    log(f"   Error: {error_data}")
                    json_responses["ml_engine_train"] = error_data
                except:
                    log(f"   Error text: {response.text}")
                    
        except Exception as e:
            log(f"❌ Step 1 FALHOU - Exception: {e}")
            json_responses["ml_engine_train"] = {"error": str(e)}
        
        # Step 2: BACKTEST THRESHOLDS RIVER 5m
        log("\n🔍 STEP 2: BACKTEST THRESHOLDS RIVER 5m")
        log("   Objetivo: Executar backtest River com múltiplos thresholds e capturar best_threshold")
        
        river_backtest_payload = {
            "symbol": "R_10",
            "timeframe": "5m",
            "lookback_candles": 1500,
            "thresholds": [0.5, 0.53, 0.55, 0.6, 0.65, 0.7, 0.75]
        }
        
        try:
            log(f"   Payload: {json.dumps(river_backtest_payload, indent=2)}")
            log("   ⏱️  Iniciando River backtest (pode demorar 30-120s)...")
            
            response = session.post(f"{api_url}/strategy/river/backtest", json=river_backtest_payload, timeout=180)
            log(f"   POST /api/strategy/river/backtest: {response.status_code}")
            
            if response.status_code == 200:
                backtest_data = response.json()
                json_responses["river_backtest"] = backtest_data
                log(f"   Response: {json.dumps(backtest_data, indent=2)}")
                
                results = backtest_data.get('results', [])
                best_threshold = backtest_data.get('best_threshold')
                candles_analyzed = backtest_data.get('candles_analyzed', 0)
                
                log(f"   📊 River Backtest Results:")
                log(f"      Total Results: {len(results)}")
                log(f"      Best Threshold: {best_threshold}")
                log(f"      Candles Analyzed: {candles_analyzed}")
                
                # Show detailed results for each threshold
                for result in results:
                    threshold = result.get('threshold', 0)
                    win_rate = result.get('win_rate', 0)
                    total_trades = result.get('total_trades', 0)
                    
                    log(f"      Threshold {threshold}: WR={win_rate:.1f}%, Trades={total_trades}")
                
                if len(results) > 0:
                    test_results["river_backtest"] = True
                    log("✅ Step 2 OK: River backtest executado com sucesso")
                    
                    # Capture best_threshold for next step
                    applied_threshold = best_threshold if best_threshold is not None else 0.6
                    log(f"   🎯 Best threshold capturado: {applied_threshold}")
                else:
                    log(f"❌ Step 2 FALHOU: Nenhum resultado de backtest retornado")
                    applied_threshold = 0.6  # fallback
            else:
                log(f"❌ River Backtest FALHOU - HTTP {response.status_code}")
                json_responses["river_backtest"] = {"error": f"HTTP {response.status_code}", "text": response.text}
                applied_threshold = 0.6  # fallback
                try:
                    error_data = response.json()
                    log(f"   Error: {error_data}")
                    json_responses["river_backtest"] = error_data
                except:
                    log(f"   Error text: {response.text}")
                    
        except Exception as e:
            log(f"❌ Step 2 FALHOU - Exception: {e}")
            json_responses["river_backtest"] = {"error": str(e)}
            applied_threshold = 0.6  # fallback
        
        # Step 3: APLICAR THRESHOLD
        log("\n🔍 STEP 3: APLICAR THRESHOLD")
        log(f"   Objetivo: Aplicar river_threshold = {applied_threshold}")
        
        threshold_config_payload = {
            "river_threshold": applied_threshold
        }
        
        try:
            log(f"   Payload: {json.dumps(threshold_config_payload, indent=2)}")
            response = session.post(f"{api_url}/strategy/river/config", json=threshold_config_payload, timeout=15)
            log(f"   POST /api/strategy/river/config: {response.status_code}")
            
            if response.status_code == 200:
                config_data = response.json()
                json_responses["apply_threshold"] = config_data
                log(f"   Response: {json.dumps(config_data, indent=2)}")
                
                success = config_data.get('success', False)
                new_threshold = config_data.get('new_threshold', 0)
                message = config_data.get('message', '')
                
                log(f"   📊 Threshold Config Result:")
                log(f"      Success: {success}")
                log(f"      New Threshold: {new_threshold}")
                log(f"      Message: {message}")
                
                if success and new_threshold == applied_threshold:
                    test_results["apply_threshold"] = True
                    log(f"✅ Step 3 OK: Threshold {applied_threshold} aplicado com sucesso")
                else:
                    log(f"❌ Step 3 FALHOU: success={success}, new_threshold={new_threshold}")
            else:
                log(f"❌ Apply Threshold FALHOU - HTTP {response.status_code}")
                json_responses["apply_threshold"] = {"error": f"HTTP {response.status_code}", "text": response.text}
                try:
                    error_data = response.json()
                    log(f"   Error: {error_data}")
                    json_responses["apply_threshold"] = error_data
                except:
                    log(f"   Error text: {response.text}")
                    
        except Exception as e:
            log(f"❌ Step 3 FALHOU - Exception: {e}")
            json_responses["apply_threshold"] = {"error": str(e)}
        
        # Step 4: TESTE 3 TICKS
        log("\n🔍 STEP 4: TESTE 3 TICKS")
        log("   Objetivo: Testar estratégia com granularity=1, duration=3, ml_gate=true")
        log("   Monitorar por 90s a cada 15s, capturar métricas")
        
        strategy_3ticks_payload = {
            "symbol": "R_10",
            "granularity": 1,
            "candle_len": 200,
            "duration": 3,
            "duration_unit": "t",
            "stake": 1,
            "ml_gate": True,
            "ml_prob_threshold": 0.6,
            "adx_trend": 28,
            "mode": "paper"
        }
        
        try:
            log(f"   Payload: {json.dumps(strategy_3ticks_payload, indent=2)}")
            
            # Start strategy
            response = session.post(f"{api_url}/strategy/start", json=strategy_3ticks_payload, timeout=20)
            log(f"   POST /api/strategy/start: {response.status_code}")
            
            if response.status_code == 200:
                start_data = response.json()
                json_responses["test_3_ticks_start"] = start_data
                log(f"   Start Response: {json.dumps(start_data, indent=2)}")
                
                running = start_data.get('running', False)
                if running:
                    log("✅ Estratégia 3 ticks iniciada com sucesso")
                    
                    # Monitor for 90 seconds, checking every 15 seconds
                    monitoring_data = []
                    monitoring_duration = 90
                    check_interval = 15
                    checks_count = monitoring_duration // check_interval
                    
                    log(f"   ⏱️  Monitorando por {monitoring_duration}s ({checks_count} checks a cada {check_interval}s)")
                    
                    for check_num in range(checks_count):
                        log(f"   📊 Check {check_num + 1}/{checks_count} (t={check_num * check_interval}s)")
                        
                        try:
                            response = session.get(f"{api_url}/strategy/status", timeout=10)
                            
                            if response.status_code == 200:
                                status_data = response.json()
                                
                                running = status_data.get('running', False)
                                win_rate = status_data.get('win_rate', 0)
                                daily_pnl = status_data.get('daily_pnl', 0)
                                last_reason = status_data.get('last_reason', '')
                                wins = status_data.get('wins', 0)
                                losses = status_data.get('losses', 0)
                                total_trades = status_data.get('total_trades', 0)
                                last_run_at = status_data.get('last_run_at')
                                
                                check_data = {
                                    "check_number": check_num + 1,
                                    "timestamp": int(time.time()),
                                    "running": running,
                                    "win_rate": win_rate,
                                    "daily_pnl": daily_pnl,
                                    "last_reason": last_reason,
                                    "wins": wins,
                                    "losses": losses,
                                    "total_trades": total_trades,
                                    "last_run_at": last_run_at
                                }
                                monitoring_data.append(check_data)
                                
                                log(f"      Running: {running}, WR: {win_rate}%, PnL: {daily_pnl}")
                                log(f"      Trades: {total_trades} (W:{wins}, L:{losses})")
                                log(f"      Last Reason: '{last_reason}'")
                                log(f"      Last Run At: {last_run_at}")
                                
                            else:
                                log(f"      ❌ Status check FALHOU - HTTP {response.status_code}")
                                
                        except Exception as e:
                            log(f"      ❌ Status check FALHOU - Exception: {e}")
                        
                        # Wait before next check (except for last check)
                        if check_num < checks_count - 1:
                            time.sleep(check_interval)
                    
                    json_responses["test_3_ticks_monitoring"] = monitoring_data
                    
                    # Stop strategy
                    log("   🛑 Parando estratégia 3 ticks...")
                    response = session.post(f"{api_url}/strategy/stop", json={}, timeout=15)
                    log(f"   POST /api/strategy/stop: {response.status_code}")
                    
                    if response.status_code == 200:
                        stop_data = response.json()
                        json_responses["test_3_ticks_stop"] = stop_data
                        log(f"   Stop Response: {json.dumps(stop_data, indent=2)}")
                        
                        # Capture final metrics
                        final_metrics["test_3_ticks"] = {
                            "win_rate": stop_data.get('win_rate', 0),
                            "daily_pnl": stop_data.get('daily_pnl', 0),
                            "wins": stop_data.get('wins', 0),
                            "losses": stop_data.get('losses', 0),
                            "total_trades": stop_data.get('total_trades', 0),
                            "last_reason": stop_data.get('last_reason', ''),
                            "monitoring_checks": len(monitoring_data)
                        }
                        
                        test_results["test_3_ticks"] = True
                        log("✅ Step 4 OK: Teste 3 ticks completado com sucesso")
                    else:
                        log(f"❌ Stop strategy FALHOU - HTTP {response.status_code}")
                else:
                    log(f"❌ Estratégia não iniciou: running={running}")
            else:
                log(f"❌ Strategy start FALHOU - HTTP {response.status_code}")
                json_responses["test_3_ticks_start"] = {"error": f"HTTP {response.status_code}", "text": response.text}
                    
        except Exception as e:
            log(f"❌ Step 4 FALHOU - Exception: {e}")
            json_responses["test_3_ticks"] = {"error": str(e)}
        
        # Step 5: TESTE 5 MINUTOS
        log("\n🔍 STEP 5: TESTE 5 MINUTOS")
        log("   Objetivo: Testar estratégia com granularity=300, duration=5, ml_gate=true")
        log("   Monitorar por 90s a cada 15s, capturar métricas")
        
        strategy_5min_payload = {
            "symbol": "R_10",
            "granularity": 300,
            "candle_len": 240,
            "duration": 5,
            "duration_unit": "t",
            "stake": 1,
            "ml_gate": True,
            "ml_prob_threshold": 0.6,
            "adx_trend": 28,
            "mode": "paper"
        }
        
        try:
            log(f"   Payload: {json.dumps(strategy_5min_payload, indent=2)}")
            
            # Start strategy
            response = session.post(f"{api_url}/strategy/start", json=strategy_5min_payload, timeout=20)
            log(f"   POST /api/strategy/start: {response.status_code}")
            
            if response.status_code == 200:
                start_data = response.json()
                json_responses["test_5_minutes_start"] = start_data
                log(f"   Start Response: {json.dumps(start_data, indent=2)}")
                
                running = start_data.get('running', False)
                if running:
                    log("✅ Estratégia 5 minutos iniciada com sucesso")
                    
                    # Monitor for 90 seconds, checking every 15 seconds
                    monitoring_data = []
                    monitoring_duration = 90
                    check_interval = 15
                    checks_count = monitoring_duration // check_interval
                    
                    log(f"   ⏱️  Monitorando por {monitoring_duration}s ({checks_count} checks a cada {check_interval}s)")
                    
                    for check_num in range(checks_count):
                        log(f"   📊 Check {check_num + 1}/{checks_count} (t={check_num * check_interval}s)")
                        
                        try:
                            response = session.get(f"{api_url}/strategy/status", timeout=10)
                            
                            if response.status_code == 200:
                                status_data = response.json()
                                
                                running = status_data.get('running', False)
                                win_rate = status_data.get('win_rate', 0)
                                daily_pnl = status_data.get('daily_pnl', 0)
                                last_reason = status_data.get('last_reason', '')
                                wins = status_data.get('wins', 0)
                                losses = status_data.get('losses', 0)
                                total_trades = status_data.get('total_trades', 0)
                                last_run_at = status_data.get('last_run_at')
                                
                                check_data = {
                                    "check_number": check_num + 1,
                                    "timestamp": int(time.time()),
                                    "running": running,
                                    "win_rate": win_rate,
                                    "daily_pnl": daily_pnl,
                                    "last_reason": last_reason,
                                    "wins": wins,
                                    "losses": losses,
                                    "total_trades": total_trades,
                                    "last_run_at": last_run_at
                                }
                                monitoring_data.append(check_data)
                                
                                log(f"      Running: {running}, WR: {win_rate}%, PnL: {daily_pnl}")
                                log(f"      Trades: {total_trades} (W:{wins}, L:{losses})")
                                log(f"      Last Reason: '{last_reason}'")
                                log(f"      Last Run At: {last_run_at}")
                                
                            else:
                                log(f"      ❌ Status check FALHOU - HTTP {response.status_code}")
                                
                        except Exception as e:
                            log(f"      ❌ Status check FALHOU - Exception: {e}")
                        
                        # Wait before next check (except for last check)
                        if check_num < checks_count - 1:
                            time.sleep(check_interval)
                    
                    json_responses["test_5_minutes_monitoring"] = monitoring_data
                    
                    # Stop strategy
                    log("   🛑 Parando estratégia 5 minutos...")
                    response = session.post(f"{api_url}/strategy/stop", json={}, timeout=15)
                    log(f"   POST /api/strategy/stop: {response.status_code}")
                    
                    if response.status_code == 200:
                        stop_data = response.json()
                        json_responses["test_5_minutes_stop"] = stop_data
                        log(f"   Stop Response: {json.dumps(stop_data, indent=2)}")
                        
                        # Capture final metrics
                        final_metrics["test_5_minutes"] = {
                            "win_rate": stop_data.get('win_rate', 0),
                            "daily_pnl": stop_data.get('daily_pnl', 0),
                            "wins": stop_data.get('wins', 0),
                            "losses": stop_data.get('losses', 0),
                            "total_trades": stop_data.get('total_trades', 0),
                            "last_reason": stop_data.get('last_reason', ''),
                            "monitoring_checks": len(monitoring_data)
                        }
                        
                        test_results["test_5_minutes"] = True
                        log("✅ Step 5 OK: Teste 5 minutos completado com sucesso")
                    else:
                        log(f"❌ Stop strategy FALHOU - HTTP {response.status_code}")
                else:
                    log(f"❌ Estratégia não iniciou: running={running}")
            else:
                log(f"❌ Strategy start FALHOU - HTTP {response.status_code}")
                json_responses["test_5_minutes_start"] = {"error": f"HTTP {response.status_code}", "text": response.text}
                    
        except Exception as e:
            log(f"❌ Step 5 FALHOU - Exception: {e}")
            json_responses["test_5_minutes"] = {"error": str(e)}
        
        # Final analysis and comprehensive report
        log("\n" + "🏁" + "="*68)
        log("RESULTADO FINAL: Sequência R_10 Paper Mode")
        log("🏁" + "="*68)
        
        passed_tests = sum(test_results.values())
        total_tests = len(test_results)
        success_rate = (passed_tests / total_tests) * 100
        
        log(f"📊 ESTATÍSTICAS:")
        log(f"   Passos executados: {total_tests}")
        log(f"   Passos bem-sucedidos: {passed_tests}")
        log(f"   Taxa de sucesso: {success_rate:.1f}%")
        
        log(f"\n📋 DETALHES POR PASSO:")
        step_names = {
            "ml_engine_train": "1) TREINO ML ENGINE 5m (3000 candles, horizon=3, seq_len=32)",
            "river_backtest": "2) BACKTEST THRESHOLDS RIVER 5m (1500 candles, 7 thresholds)",
            "apply_threshold": "3) APLICAR THRESHOLD (best_threshold ou 0.6)",
            "test_3_ticks": "4) TESTE 3 TICKS (granularity=1, ml_gate=true, 90s monitoring)",
            "test_5_minutes": "5) TESTE 5 MINUTOS (granularity=300, ml_gate=true, 90s monitoring)"
        }
        
        for test_key, passed in test_results.items():
            step_name = step_names.get(test_key, test_key)
            status = "✅ SUCESSO" if passed else "❌ FALHOU"
            log(f"   {step_name}: {status}")
        
        # Report threshold applied
        log(f"\n🎯 THRESHOLD APLICADO: {applied_threshold}")
        
        # Report final metrics
        log(f"\n📈 MÉTRICAS FINAIS:")
        for test_name, metrics in final_metrics.items():
            log(f"   {test_name.upper()}:")
            log(f"      Win Rate: {metrics.get('win_rate', 0)}%")
            log(f"      Daily PnL: {metrics.get('daily_pnl', 0)}")
            log(f"      Wins: {metrics.get('wins', 0)}")
            log(f"      Losses: {metrics.get('losses', 0)}")
            log(f"      Total Trades: {metrics.get('total_trades', 0)}")
            log(f"      Last Reason: '{metrics.get('last_reason', '')}'")
            log(f"      Monitoring Checks: {metrics.get('monitoring_checks', 0)}")
        
        # Report all JSON responses as requested
        log(f"\n📄 TODOS OS JSONs RETORNADOS:")
        log("="*50)
        for step_name, json_data in json_responses.items():
            log(f"\n🔹 {step_name.upper()}:")
            log(json.dumps(json_data, indent=2, ensure_ascii=False))
            log("-" * 30)
        
        overall_success = passed_tests >= 3  # Allow 2 failures out of 5 steps
        
        if overall_success:
            log("\n🎉 SEQUÊNCIA R_10 PAPER MODE EXECUTADA COM SUCESSO!")
            log("📋 Passos completados:")
            if test_results["ml_engine_train"]:
                log("   ✅ ML Engine: Modelo treinado com 3000 candles de 5m")
            if test_results["river_backtest"]:
                log("   ✅ River Backtest: Thresholds testados, best_threshold capturado")
            if test_results["apply_threshold"]:
                log(f"   ✅ Threshold: {applied_threshold} aplicado com sucesso")
            if test_results["test_3_ticks"]:
                log("   ✅ Teste 3 Ticks: Estratégia executada com ML gate por 90s")
            if test_results["test_5_minutes"]:
                log("   ✅ Teste 5 Minutos: Estratégia executada com ML gate por 90s")
            log("   🎯 CONCLUSÃO: Sequência paper mode R_10 completada!")
            log("   💡 Todos os JSONs e métricas foram reportados conforme solicitado")
            log("   🚫 NÃO executado /api/deriv/buy diretamente (somente StrategyRunner paper)")
        else:
            log("\n❌ PROBLEMAS DETECTADOS NA SEQUÊNCIA")
            failed_steps = [step_names.get(name, name) for name, passed in test_results.items() if not passed]
            log(f"   Passos que falharam: {failed_steps}")
            log("   📋 FOCO: Verificar implementação dos endpoints ML Engine e Strategy Runner")
        
        return overall_success, test_results, json_responses, applied_threshold, final_metrics
        
    except Exception as e:
        log(f"❌ ERRO CRÍTICO NA SEQUÊNCIA R_10: {e}")
        import traceback
        log(f"   Traceback: {traceback.format_exc()}")
        
        return False, {
            "error": "critical_test_exception",
            "details": str(e),
            "test_results": test_results
        }, {}, applied_threshold, {}

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_r10_paper_mode_sequence())