#!/usr/bin/env python3
"""
WebSocket Ticks Test for Deriv Trading Bot
Tests WebSocket /api/ws/ticks as requested in Portuguese review
"""

import asyncio
import websockets
import json
import time
from datetime import datetime

async def test_websocket_ticks():
    """Test WebSocket /api/ws/ticks for tick functionality"""
    
    print("🔍 TESTE WEBSOCKET TICKS - CONFORME REVIEW REQUEST")
    print("="*70)
    print("📋 Objetivo: Testar WebSocket /api/ws/ticks para verificar se ticks chegam corretamente")
    print("📋 Símbolos: R_100, R_75, R_50")
    print("📋 Duração: 30 segundos")
    print("📋 Verificar se ticks funcionam em 'entrada automática'")
    
    ws_url = "wss://market-bot-fix.preview.emergentagent.com/api/ws/ticks?symbols=R_100,R_75,R_50"
    print(f"   WebSocket URL: {ws_url}")
    
    messages_received = 0
    tick_messages = 0
    heartbeat_messages = 0
    symbols_detected = set()
    start_time = time.time()
    test_duration = 30
    
    try:
        print("🔌 Conectando ao WebSocket...")
        
        websocket = await websockets.connect(ws_url)
        print("✅ WebSocket conectado com sucesso")
        
        try:
            # Send initial payload
            initial_payload = {"symbols": ["R_100", "R_75", "R_50"]}
            await websocket.send(json.dumps(initial_payload))
            print(f"📤 Payload inicial enviado: {initial_payload}")
            
            print(f"⏱️  Monitorando por {test_duration} segundos...")
            
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
                        
                        # Log every 10th message to show progress
                        if messages_received % 10 == 0 or messages_received <= 5:
                            elapsed = time.time() - start_time
                            rate = messages_received / elapsed if elapsed > 0 else 0
                            print(f"📊 Progresso: {messages_received} msgs ({tick_messages} ticks, {heartbeat_messages} heartbeats) em {elapsed:.1f}s - {rate:.2f} msg/s")
                            if symbol != 'unknown':
                                print(f"   Último tick: {symbol} = {price}")
                        
                    except json.JSONDecodeError:
                        print(f"⚠️  Mensagem não-JSON recebida: {message[:100]}...")
                        
                except asyncio.TimeoutError:
                    elapsed = time.time() - start_time
                    print(f"⚠️  Timeout aguardando mensagem (elapsed: {elapsed:.1f}s)")
                    
                except websockets.exceptions.ConnectionClosed as e:
                    print(f"❌ WebSocket fechou inesperadamente: {e}")
                    break
                    
                except Exception as e:
                    print(f"❌ Erro durante recepção: {e}")
                    
        finally:
            await websocket.close()
            
    except Exception as e:
        print(f"❌ Erro inesperado no WebSocket: {e}")
        return False
    
    # Analysis
    elapsed_time = time.time() - start_time
    message_rate = messages_received / elapsed_time if elapsed_time > 0 else 0
    tick_rate = tick_messages / elapsed_time if elapsed_time > 0 else 0
    
    print(f"\n📊 ANÁLISE DETALHADA DO WEBSOCKET:")
    print(f"   Tempo de teste: {elapsed_time:.1f}s")
    print(f"   Total mensagens: {messages_received}")
    print(f"   Mensagens de tick: {tick_messages}")
    print(f"   Mensagens de heartbeat: {heartbeat_messages}")
    print(f"   Taxa total: {message_rate:.2f} msg/s")
    print(f"   Taxa de ticks: {tick_rate:.2f} ticks/s")
    print(f"   Símbolos detectados: {list(symbols_detected)}")
    
    # Determine success
    is_working = True
    issues = []
    
    if messages_received == 0:
        is_working = False
        issues.append("Nenhuma mensagem recebida")
    elif tick_messages == 0:
        is_working = False
        issues.append("Nenhum tick recebido")
    elif message_rate < 0.3:
        is_working = False
        issues.append(f"Taxa muito baixa: {message_rate:.2f} msg/s")
    elif not symbols_detected.intersection({"R_100", "R_75", "R_50"}):
        is_working = False
        issues.append("Nenhum dos símbolos esperados detectado")
    
    if is_working:
        print("✅ WEBSOCKET TICKS FUNCIONANDO!")
        print(f"   ✓ Conexão mantida por {elapsed_time:.1f}s")
        print(f"   ✓ Taxa: {message_rate:.2f} msg/s")
        print(f"   ✓ Ticks recebidos: {tick_messages} de símbolos {list(symbols_detected)}")
        print("   ✓ Ticks funcionam corretamente para entrada automática")
    else:
        print("❌ WEBSOCKET TICKS COM PROBLEMAS:")
        for issue in issues:
            print(f"   - {issue}")
        print("   ❌ Ticks NÃO funcionam adequadamente para entrada automática")
    
    return is_working

async def main():
    """Main function"""
    success = await test_websocket_ticks()
    
    if success:
        print("\n🎉 TESTE WEBSOCKET TICKS: SUCESSO")
        print("   Ticks funcionam corretamente em entrada automática")
    else:
        print("\n❌ TESTE WEBSOCKET TICKS: FALHA")
        print("   Problema detectado com ticks em entrada automática")
    
    return success

if __name__ == "__main__":
    result = asyncio.run(main())
    exit(0 if result else 1)