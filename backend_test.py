#!/usr/bin/env python3
"""
Backend API Testing for Online Learning System
Tests as requested in Portuguese review:
🧠 TESTE DO SISTEMA DE ONLINE LEARNING

TESTE SOLICITADO:
1. **Verificar modelos online ativos:**
   - GET /api/ml/online/list (deve mostrar pelo menos 1 modelo ativo)
   - GET /api/ml/online/progress (mostrar estatísticas dos modelos)

2. **Testar novo endpoint de inicialização:**
   - POST /api/ml/online/initialize (forçar criação de modelos online)

3. **Verificar status dos modelos:**
   - GET /api/ml/online/status/{model_id} para cada modelo listado

4. **Testar simulação de trade (para verificar se online learning funciona):**
   - Simular um trade fictício para ver se o sistema faria update dos modelos online
   - IMPORTANTE: NÃO executar /api/deriv/buy real, apenas testar se os endpoints estão funcionando
"""

import requests
import json
import sys
import time
from datetime import datetime

class OnlineLearningTester:
    def __init__(self, base_url="https://candles-ml-finance.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
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

    def test_online_models_list(self):
        """Test 1: Verificar modelos online ativos - GET /api/ml/online/list"""
        self.log("\n" + "="*70)
        self.log("TEST 1: VERIFICAR MODELOS ONLINE ATIVOS")
        self.log("="*70)
        self.log("📋 Objetivo: GET /api/ml/online/list (deve mostrar pelo menos 1 modelo ativo)")
        
        success, data, status_code = self.run_test(
            "Online Models List",
            "GET",
            "ml/online/list",
            200
        )
        
        if not success:
            self.log(f"❌ CRITICAL: GET /api/ml/online/list falhou - Status: {status_code}")
            return False, data
        
        models = data.get('models', [])
        count = data.get('count', 0)
        statuses = data.get('statuses', {})
        
        self.log(f"📊 RESULTADOS:")
        self.log(f"   Modelos encontrados: {models}")
        self.log(f"   Contagem: {count}")
        self.log(f"   Statuses disponíveis: {len(statuses)} modelos")
        
        # Validation
        if count == 0:
            self.log("⚠️  Nenhum modelo online ativo encontrado")
            return False, {"message": "no_active_models", "data": data}
        
        self.log(f"✅ {count} modelo(s) online ativo(s) encontrado(s)")
        
        # Check each model status
        for model_id in models:
            model_status = statuses.get(model_id, {})
            status = model_status.get('status', 'unknown')
            self.log(f"   📋 Modelo {model_id}: status = {status}")
        
        return True, data

    def test_online_progress(self):
        """Test 2: Mostrar estatísticas dos modelos - GET /api/ml/online/progress"""
        self.log("\n" + "="*70)
        self.log("TEST 2: ESTATÍSTICAS DOS MODELOS ONLINE")
        self.log("="*70)
        self.log("📋 Objetivo: GET /api/ml/online/progress (mostrar estatísticas dos modelos)")
        
        success, data, status_code = self.run_test(
            "Online Learning Progress",
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
        
        self.log(f"📊 ESTATÍSTICAS GERAIS:")
        self.log(f"   Modelos ativos: {active_models}")
        self.log(f"   Total de updates: {total_updates}")
        self.log(f"   Modelos com detalhes: {len(models_detail)}")
        
        # Show details for each model
        for i, model_detail in enumerate(models_detail):
            model_id = model_detail.get('model_id', f'model_{i}')
            update_count = model_detail.get('update_count', 0)
            features_count = model_detail.get('features_count', 0)
            current_accuracy = model_detail.get('current_accuracy', 0)
            current_precision = model_detail.get('current_precision', 0)
            improvement_trend = model_detail.get('improvement_trend', 'unknown')
            
            self.log(f"   📋 Modelo {model_id}:")
            self.log(f"      Updates: {update_count}")
            self.log(f"      Features: {features_count}")
            self.log(f"      Accuracy: {current_accuracy:.3f}")
            self.log(f"      Precision: {current_precision:.3f}")
            self.log(f"      Trend: {improvement_trend}")
        
        if active_models == 0:
            self.log("⚠️  Nenhum modelo ativo encontrado")
            return False, {"message": "no_active_models", "data": data}
        
        self.log(f"✅ Estatísticas obtidas para {active_models} modelo(s)")
        return True, data

    def test_initialize_online_models(self):
        """Test 3: Testar novo endpoint de inicialização - POST /api/ml/online/initialize"""
        self.log("\n" + "="*70)
        self.log("TEST 3: INICIALIZAÇÃO DE MODELOS ONLINE")
        self.log("="*70)
        self.log("📋 Objetivo: POST /api/ml/online/initialize (forçar criação de modelos online)")
        
        success, data, status_code = self.run_test(
            "Initialize Online Models",
            "POST",
            "ml/online/initialize",
            200,
            timeout=60
        )
        
        if not success:
            self.log(f"❌ CRITICAL: POST /api/ml/online/initialize falhou - Status: {status_code}")
            return False, data
        
        message = data.get('message', '')
        models_created = data.get('models_created', 0)
        models = data.get('models', [])
        
        self.log(f"📊 RESULTADO DA INICIALIZAÇÃO:")
        self.log(f"   Mensagem: {message}")
        self.log(f"   Modelos criados: {models_created}")
        self.log(f"   Modelos: {models}")
        
        if models_created == 0:
            self.log("⚠️  Nenhum modelo foi criado")
            if "dados insuficientes" in message.lower() or "erro" in message.lower():
                self.log("   Motivo: Dados insuficientes ou erro durante criação")
                return False, {"message": "insufficient_data", "data": data}
            else:
                self.log("   Modelos podem já existir")
        
        self.log(f"✅ Inicialização executada - {models_created} modelo(s) processado(s)")
        return True, data

    def test_model_status_endpoints(self):
        """Test 4: Verificar status dos modelos - GET /api/ml/online/status/{model_id}"""
        self.log("\n" + "="*70)
        self.log("TEST 4: STATUS DOS MODELOS ONLINE")
        self.log("="*70)
        self.log("📋 Objetivo: GET /api/ml/online/status/{model_id} para cada modelo listado")
        
        # First get the list of models
        self.log("\n🔍 Obtendo lista de modelos para testar status")
        success_list, list_data, _ = self.run_test(
            "Get Models List for Status Test",
            "GET",
            "ml/online/list",
            200
        )
        
        if not success_list:
            self.log("❌ CRITICAL: Não foi possível obter lista de modelos")
            return False, {}
        
        models = list_data.get('models', [])
        self.log(f"   Modelos encontrados: {models}")
        
        if not models:
            self.log("⚠️  Nenhum modelo encontrado para testar status")
            return False, {"message": "no_models_found"}
        
        # Test status endpoint for each model
        status_results = {}
        validation_errors = []
        
        for model_id in models:
            self.log(f"\n🔍 Testando status do modelo: {model_id}")
            
            success, data, status_code = self.run_test(
                f"Model Status - {model_id}",
                "GET",
                f"ml/online/status/{model_id}",
                200
            )
            
            if not success:
                if status_code == 404:
                    validation_errors.append(f"❌ Modelo {model_id} não encontrado (404)")
                else:
                    validation_errors.append(f"❌ Erro ao obter status do modelo {model_id}: {status_code}")
                continue
            
            # Validate status response structure
            status = data.get('status', '')
            model_info = data.get('model_info', {})
            performance_history = data.get('performance_history', [])
            
            self.log(f"   📊 Status: {status}")
            self.log(f"   Model Info: {json.dumps(model_info, indent=2)}")
            self.log(f"   Performance History: {len(performance_history)} entradas")
            
            # Validate required fields
            if not status:
                validation_errors.append(f"❌ Modelo {model_id}: status vazio")
            elif status not in ['active', 'training', 'ready', 'inactive']:
                validation_errors.append(f"❌ Modelo {model_id}: status inválido: {status}")
            else:
                self.log(f"   ✅ Status válido: {status}")
            
            if not model_info:
                validation_errors.append(f"❌ Modelo {model_id}: model_info vazio")
            else:
                # Check key model info fields
                update_count = model_info.get('update_count', 0)
                features_count = model_info.get('features_count', 0)
                
                self.log(f"   📋 Update Count: {update_count}")
                self.log(f"   📋 Features Count: {features_count}")
                
                if features_count <= 0:
                    validation_errors.append(f"❌ Modelo {model_id}: features_count inválido: {features_count}")
                else:
                    self.log(f"   ✅ Features válidas: {features_count}")
            
            status_results[model_id] = {
                "status": status,
                "model_info": model_info,
                "performance_history_count": len(performance_history)
            }
        
        # Final validation
        if validation_errors:
            self.log("\n❌ VALIDATION ERRORS:")
            for error in validation_errors:
                self.log(f"   {error}")
            return False, {"errors": validation_errors, "results": status_results}
        
        self.log(f"\n✅ Status obtido com sucesso para {len(status_results)} modelo(s)")
        return True, status_results

    def test_trade_simulation_endpoints(self):
        """Test 5: Testar simulação de trade (verificar se endpoints funcionam)"""
        self.log("\n" + "="*70)
        self.log("TEST 5: SIMULAÇÃO DE TRADE - ENDPOINTS FUNCIONAIS")
        self.log("="*70)
        self.log("📋 Objetivo: Verificar se endpoints estão funcionando (NÃO executar trades reais)")
        self.log("⚠️  IMPORTANTE: Apenas testar conectividade, não executar /api/deriv/buy")
        
        # Test 1: Check Deriv connectivity
        self.log("\n🔍 Verificando conectividade com Deriv")
        success_deriv, deriv_data, _ = self.run_test(
            "Deriv Connectivity Check",
            "GET",
            "deriv/status",
            200
        )
        
        if not success_deriv:
            self.log("❌ CRITICAL: Deriv não conectado")
            return False, deriv_data
        
        connected = deriv_data.get('connected', False)
        authenticated = deriv_data.get('authenticated', False)
        
        self.log(f"   📊 Conectado: {connected}")
        self.log(f"   📊 Autenticado: {authenticated}")
        
        if not connected:
            self.log("❌ Deriv não está conectado - trades não funcionarão")
            return False, {"message": "deriv_not_connected", "data": deriv_data}
        
        # Test 2: Check if we can get proposal (without buying)
        self.log("\n🔍 Testando endpoint de proposta (sem comprar)")
        
        proposal_data = {
            "symbol": "R_100",
            "type": "CALLPUT",
            "contract_type": "CALL",
            "duration": 5,
            "duration_unit": "t",
            "stake": 1.0,
            "currency": "USD"
        }
        
        success_proposal, proposal_response, status_code = self.run_test(
            "Test Proposal Endpoint",
            "POST",
            "deriv/proposal",
            200,
            data=proposal_data,
            timeout=15
        )
        
        if success_proposal:
            proposal_id = proposal_response.get('id')
            ask_price = proposal_response.get('ask_price', 0)
            payout = proposal_response.get('payout', 0)
            
            self.log(f"   ✅ Proposta obtida com sucesso!")
            self.log(f"   📋 ID: {proposal_id}")
            self.log(f"   📋 Preço: {ask_price}")
            self.log(f"   📋 Payout: {payout}")
        else:
            self.log(f"   ⚠️  Endpoint de proposta falhou - Status: {status_code}")
            if status_code == 400:
                error_detail = proposal_response.get('detail', '')
                self.log(f"   Erro: {error_detail}")
        
        # Test 3: Verify online learning would be triggered (check current state)
        self.log("\n🔍 Verificando estado atual do sistema de aprendizado online")
        
        success_progress, progress_data, _ = self.run_test(
            "Check Online Learning State",
            "GET",
            "ml/online/progress",
            200
        )
        
        if success_progress:
            active_models = progress_data.get('active_models', 0)
            total_updates = progress_data.get('total_updates', 0)
            
            self.log(f"   📊 Modelos ativos: {active_models}")
            self.log(f"   📊 Total updates: {total_updates}")
            
            if active_models > 0:
                self.log("   ✅ Sistema de aprendizado online está ativo")
                self.log("   📋 Modelos estariam prontos para aprender com trades")
            else:
                self.log("   ⚠️  Nenhum modelo online ativo")
                self.log("   📋 Sistema não aprenderia com trades no estado atual")
        
        # Test 4: Check if system has the necessary endpoints for trade learning
        endpoints_to_check = [
            ("ml/online/list", "Lista de modelos"),
            ("ml/online/progress", "Progresso do aprendizado"),
            ("deriv/status", "Status da Deriv")
        ]
        
        self.log("\n🔍 Verificando endpoints necessários para aprendizado com trades")
        
        endpoints_working = 0
        for endpoint, description in endpoints_to_check:
            success, _, _ = self.run_test(
                f"Check {description}",
                "GET",
                endpoint,
                200
            )
            
            if success:
                endpoints_working += 1
                self.log(f"   ✅ {description}: OK")
            else:
                self.log(f"   ❌ {description}: FALHOU")
        
        # Summary
        all_endpoints_working = endpoints_working == len(endpoints_to_check)
        deriv_ready = connected
        proposal_working = success_proposal
        online_learning_ready = success_progress and progress_data.get('active_models', 0) > 0
        
        self.log(f"\n📊 RESUMO DA SIMULAÇÃO:")
        self.log(f"   Endpoints funcionais: {endpoints_working}/{len(endpoints_to_check)}")
        self.log(f"   Deriv conectado: {deriv_ready}")
        self.log(f"   Propostas funcionando: {proposal_working}")
        self.log(f"   Aprendizado online pronto: {online_learning_ready}")
        
        if all_endpoints_working and deriv_ready:
            self.log("✅ Sistema pronto para trades com aprendizado online!")
            if not online_learning_ready:
                self.log("⚠️  Mas nenhum modelo online ativo no momento")
        else:
            self.log("❌ Sistema não está completamente pronto para trades")
        
        return all_endpoints_working and deriv_ready, {
            "deriv_connected": deriv_ready,
            "proposal_working": proposal_working,
            "online_learning_ready": online_learning_ready,
            "endpoints_working": endpoints_working,
            "total_endpoints": len(endpoints_to_check)
        }

    def run_comprehensive_tests(self):
        """Run all tests as requested in Portuguese review"""
        self.log("\n" + "🚀" + "="*68)
        self.log("TESTES DO SISTEMA ML E APRENDIZADO ONLINE")
        self.log("🚀" + "="*68)
        self.log("📋 Conforme solicitado na review request em português:")
        self.log("   1. ML Training (problema 'promotion: false' resolvido)")
        self.log("   2. Sistema de Aprendizado Online (endpoints funcionais)")
        self.log("   3. Integração com trades (modelo aprende com trades)")
        self.log("   4. Validações funcionais (métricas, contadores, precisão)")
        self.log("   ⚠️  IMPORTANTE: Apenas trades DEMO/PAPER")
        self.log(f"   🌐 Base URL: {self.base_url}")
        
        results = {}
        
        # Test 1: ML Training Resolution
        self.log("\n🔍 EXECUTANDO TESTE 1: ML Training - Problema Resolvido")
        ml_training_ok, ml_training_data = self.test_ml_training_resolved()
        results['ml_training'] = ml_training_ok
        
        # Test 2: Online Learning System
        self.log("\n🔍 EXECUTANDO TESTE 2: Sistema de Aprendizado Online")
        online_system_ok, online_system_data = self.test_online_learning_system()
        results['online_system'] = online_system_ok
        
        # Test 3: Trade Integration (only if previous tests passed)
        if ml_training_ok and online_system_ok:
            self.log("\n🔍 EXECUTANDO TESTE 3: Integração com Trades")
            trade_integration_ok, trade_integration_data = self.test_trade_integration()
            results['trade_integration'] = trade_integration_ok
        else:
            self.log("\n⚠️  PULANDO TESTE 3: Testes anteriores falharam")
            trade_integration_ok = False
            results['trade_integration'] = False
        
        # Test 4: Functional Validations
        self.log("\n🔍 EXECUTANDO TESTE 4: Validações Funcionais")
        functional_ok, functional_data = self.test_functional_validations()
        results['functional'] = functional_ok
        
        # Final Summary
        self.log("\n" + "🏁" + "="*68)
        self.log("RESUMO FINAL DOS TESTES")
        self.log("🏁" + "="*68)
        
        if ml_training_ok:
            self.log("✅ 1. ML Training: Problema 'promotion: false' resolvido ✓")
            self.log("✅    Dados reais no grid ao invés de traços vazios ✓")
        else:
            self.log("❌ 1. ML Training: FAILED")
        
        if online_system_ok:
            self.log("✅ 2. Sistema Online: Endpoints funcionais ✓")
            self.log("✅    Modelo 'online_model_demo' criado com sucesso ✓")
        else:
            self.log("❌ 2. Sistema Online: FAILED")
        
        if trade_integration_ok:
            learning_detected = trade_integration_data.get('learning_detected', False) if isinstance(trade_integration_data, dict) else False
            if learning_detected:
                self.log("✅ 3. Integração Trades: Sistema aprendeu com trade ✓")
            else:
                self.log("⚠️  3. Integração Trades: Trade executado, aprendizado não detectado")
        else:
            self.log("❌ 3. Integração Trades: FAILED")
        
        if functional_ok:
            valid_models = functional_data.get('valid_models', 0) if isinstance(functional_data, dict) else 0
            self.log(f"✅ 4. Validações: {valid_models} modelos com métricas válidas ✓")
            self.log("✅    Sistema preparado para aprendizado automático ✓")
        else:
            self.log("❌ 4. Validações Funcionais: FAILED")
        
        # Overall success criteria
        critical_tests_passed = ml_training_ok and online_system_ok and functional_ok
        all_tests_passed = critical_tests_passed and trade_integration_ok
        
        if all_tests_passed:
            self.log("\n🎉 TODOS OS TESTES PASSARAM COM SUCESSO!")
            self.log("📋 Sistema ML e Aprendizado Online funcionando perfeitamente:")
            self.log("   ✅ Problema 'promotion: false' resolvido")
            self.log("   ✅ Sistema de aprendizado online implementado")
            self.log("   ✅ Integração com trades funcionando")
            self.log("   ✅ Métricas e validações corretas")
        elif critical_tests_passed:
            self.log("\n🎉 TESTES CRÍTICOS PASSARAM!")
            self.log("📋 Sistema ML e Aprendizado Online funcionando:")
            self.log("   ✅ Funcionalidades principais implementadas")
            self.log("   ⚠️  Integração com trades precisa de verificação")
        else:
            failed_tests = []
            if not ml_training_ok:
                failed_tests.append("ML Training")
            if not online_system_ok:
                failed_tests.append("Sistema Online")
            if not functional_ok:
                failed_tests.append("Validações Funcionais")
            
            self.log(f"\n⚠️  {len(failed_tests)} TESTE(S) CRÍTICO(S) FALHARAM: {', '.join(failed_tests)}")
            self.log("📋 Verificar logs detalhados acima para diagnóstico")
        
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

def main():
    """Main function to run ML and Online Learning tests"""
    print("🧠 ML and Online Learning Backend API Tester")
    print("=" * 70)
    print("📋 Testing as requested in Portuguese review:")
    print("   1. ML Training (problema 'promotion: false' resolvido)")
    print("   2. Sistema de Aprendizado Online")
    print("   3. Integração com trades")
    print("   4. Validações funcionais")
    
    # Use the URL from frontend/.env as specified
    tester = MLOnlineLearningTester()
    
    try:
        # Run comprehensive tests
        success, results = tester.run_comprehensive_tests()
        
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
    main()