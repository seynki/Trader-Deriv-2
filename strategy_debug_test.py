#!/usr/bin/env python3
"""
Strategy Debug Test - Check if strategy is processing ticks and making analyses
"""

import requests
import json
import time
from datetime import datetime

def test_strategy_analysis():
    """Test if strategy is analyzing market conditions and making decisions"""
    
    print("🔍 TESTE DE ANÁLISE DA ESTRATÉGIA")
    print("="*70)
    print("📋 Objetivo: Verificar se a estratégia está processando ticks e fazendo análises")
    print("📋 Verificar se está fazendo contratos (paper mode)")
    
    base_url = "https://deriv-trade-bot-3.preview.emergentagent.com/api"
    
    # 1. Check initial strategy status
    print("\n1. VERIFICANDO STATUS INICIAL DA ESTRATÉGIA")
    response = requests.get(f"{base_url}/strategy/status")
    if response.status_code == 200:
        initial_status = response.json()
        print(f"   Status inicial: {json.dumps(initial_status, indent=2)}")
        
        initial_trades = initial_status.get('total_trades', 0)
        initial_pnl = initial_status.get('daily_pnl', 0.0)
        running = initial_status.get('running', False)
        last_run_at = initial_status.get('last_run_at')
        
        print(f"   Running: {running}")
        print(f"   Total trades: {initial_trades}")
        print(f"   Daily PnL: {initial_pnl}")
        print(f"   Last run at: {last_run_at}")
        
        if not running:
            print("   ⚠️  Estratégia não está rodando - iniciando...")
            
            # Start strategy
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
            
            start_response = requests.post(f"{base_url}/strategy/start", json=payload)
            if start_response.status_code == 200:
                print("   ✅ Estratégia iniciada com sucesso")
            else:
                print(f"   ❌ Falha ao iniciar estratégia: {start_response.status_code}")
                return False
    else:
        print(f"   ❌ Falha ao obter status: {response.status_code}")
        return False
    
    # 2. Monitor for 2 minutes to see if strategy makes any trades
    print("\n2. MONITORANDO ATIVIDADE DA ESTRATÉGIA POR 2 MINUTOS")
    print("   Verificando se a estratégia faz análises e executa trades...")
    
    monitor_duration = 120  # 2 minutes
    start_time = time.time()
    check_interval = 15  # Check every 15 seconds
    
    last_run_timestamps = []
    trade_activity = []
    
    while time.time() - start_time < monitor_duration:
        elapsed = time.time() - start_time
        
        # Get current status
        response = requests.get(f"{base_url}/strategy/status")
        if response.status_code == 200:
            current_status = response.json()
            
            current_trades = current_status.get('total_trades', 0)
            current_pnl = current_status.get('daily_pnl', 0.0)
            current_running = current_status.get('running', False)
            current_last_run = current_status.get('last_run_at')
            
            # Track activity
            if current_last_run:
                last_run_timestamps.append((elapsed, current_last_run))
            
            if current_trades != initial_trades:
                trade_activity.append({
                    'elapsed': elapsed,
                    'trades': current_trades,
                    'pnl': current_pnl,
                    'trade_change': current_trades - initial_trades
                })
            
            print(f"   [{elapsed:.0f}s] Running: {current_running}, Trades: {current_trades} (+{current_trades - initial_trades}), PnL: {current_pnl:.2f}, Last run: {current_last_run}")
            
            # Check if strategy stopped unexpectedly
            if not current_running:
                print(f"   ❌ CRÍTICO: Estratégia parou de rodar após {elapsed:.1f}s!")
                break
                
        else:
            print(f"   ⚠️  Erro ao obter status: {response.status_code}")
        
        time.sleep(check_interval)
    
    # 3. Analysis
    print(f"\n3. ANÁLISE DOS RESULTADOS ({time.time() - start_time:.1f}s de monitoramento)")
    
    # Check if strategy is actively running (last_run_at updating)
    if len(last_run_timestamps) >= 2:
        first_timestamp = last_run_timestamps[0][1]
        last_timestamp = last_run_timestamps[-1][1]
        unique_timestamps = len(set(ts[1] for ts in last_run_timestamps))
        
        print(f"   Timestamps de execução:")
        print(f"     Primeiro: {first_timestamp}")
        print(f"     Último: {last_timestamp}")
        print(f"     Atualizações únicas: {unique_timestamps}")
        
        if unique_timestamps >= 3:
            print("   ✅ Estratégia está executando regularmente (last_run_at atualizando)")
        else:
            print("   ⚠️  Estratégia pode não estar executando adequadamente")
    else:
        print("   ❌ Poucos dados de execução capturados")
    
    # Check trade activity
    if trade_activity:
        print(f"   Atividade de trades detectada:")
        for activity in trade_activity:
            print(f"     {activity['elapsed']:.0f}s: +{activity['trade_change']} trades, PnL: {activity['pnl']:.2f}")
        print("   ✅ ESTRATÉGIA ESTÁ FAZENDO CONTRATOS!")
    else:
        print("   ⚠️  Nenhuma atividade de trade detectada durante o monitoramento")
        print("   Isso pode indicar:")
        print("     - Condições de mercado não atendem aos critérios da estratégia")
        print("     - Estratégia não está processando dados adequadamente")
        print("     - Problemas na obtenção de dados de mercado")
    
    # 4. Check if strategy is getting market data
    print("\n4. VERIFICANDO ACESSO A DADOS DE MERCADO")
    
    # Test candles endpoint
    candles_response = requests.post(f"{base_url}/candles/ingest?symbol=R_100&granularity=60&count=50")
    if candles_response.status_code == 200:
        candles_data = candles_response.json()
        received = candles_data.get('received', 0)
        print(f"   ✅ Dados de mercado disponíveis: {received} candles recebidos")
    else:
        print(f"   ❌ Problema ao obter dados de mercado: {candles_response.status_code}")
    
    # 5. Final assessment
    print(f"\n5. DIAGNÓSTICO FINAL")
    
    # Get final status
    final_response = requests.get(f"{base_url}/strategy/status")
    if final_response.status_code == 200:
        final_status = final_response.json()
        final_trades = final_status.get('total_trades', 0)
        final_running = final_status.get('running', False)
        
        if final_running:
            print("   ✅ Estratégia continua rodando")
        else:
            print("   ❌ Estratégia parou de rodar")
        
        if final_trades > initial_trades:
            print(f"   ✅ CONTRATOS EXECUTADOS: {final_trades - initial_trades} novos trades")
            print("   🎯 PROBLEMA RESOLVIDO: Bot está fazendo contratos automaticamente")
        else:
            print("   ⚠️  NENHUM CONTRATO EXECUTADO durante o teste")
            print("   🔍 POSSÍVEIS CAUSAS:")
            print("     1. Condições de mercado não atendem aos critérios técnicos")
            print("     2. Parâmetros da estratégia muito restritivos")
            print("     3. Problemas na análise técnica ou obtenção de dados")
            print("     4. Estratégia não está processando ticks adequadamente")
            
            # Suggest diagnosis
            if len(last_run_timestamps) >= 3:
                print("   📊 DIAGNÓSTICO: Estratégia está rodando mas não encontra sinais de entrada")
                print("   💡 SUGESTÃO: Condições de mercado podem não estar atendendo aos critérios")
            else:
                print("   📊 DIAGNÓSTICO: Estratégia pode não estar processando dados adequadamente")
                print("   💡 SUGESTÃO: Verificar logs do backend para erros na análise técnica")
    
    return final_trades > initial_trades if 'final_trades' in locals() else False

if __name__ == "__main__":
    success = test_strategy_analysis()
    
    if success:
        print("\n🎉 TESTE CONCLUÍDO: Estratégia está fazendo contratos")
    else:
        print("\n⚠️  TESTE CONCLUÍDO: Estratégia não fez contratos durante o teste")
        print("   Isso pode ser normal se as condições de mercado não atenderem aos critérios")
    
    exit(0 if success else 1)