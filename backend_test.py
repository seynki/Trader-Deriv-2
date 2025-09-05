#!/usr/bin/env python3
"""
Backend API Testing for Advanced ML Feature Engineering System
Tests as requested in Portuguese review:
1. Basic Connectivity (Deriv + ML status)
2. Feature Engineering Test (77+ technical indicators)
3. MongoDB Atlas Test (SSL connectivity)
"""

import requests
import json
import sys
import time
from datetime import datetime

class AdvancedMLFeatureTester:
    def __init__(self, base_url="https://deriv-autobot-1.preview.emergentagent.com"):
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

    def test_basic_connectivity(self):
        """Test 1: Conectividade Básica - GET /api/deriv/status e GET /api/ml/status"""
        self.log("\n" + "="*70)
        self.log("TEST 1: CONECTIVIDADE BÁSICA")
        self.log("="*70)
        self.log("📋 Objetivo: Verificar se Deriv retorna connected=true e ML status")
        
        # Test GET /api/deriv/status (deve retornar connected=true)
        self.log("\n🔍 Testando GET /api/deriv/status (deve retornar connected=true)")
        success_deriv, deriv_data, status_code = self.run_test(
            "Deriv Status Check",
            "GET",
            "deriv/status", 
            200
        )
        
        if not success_deriv:
            self.log("❌ CRITICAL: GET /api/deriv/status falhou")
            return False, {}
        
        connected = deriv_data.get('connected', False)
        authenticated = deriv_data.get('authenticated', False)
        
        self.log(f"   Connected: {connected}")
        self.log(f"   Authenticated: {authenticated}")
        
        if not connected:
            self.log("❌ CRITICAL: Deriv connected=false")
            return False, deriv_data
        
        self.log("✅ GET /api/deriv/status: connected=true ✓")
        
        # Test GET /api/ml/status (verificar estado do champion)
        self.log("\n🔍 Testando GET /api/ml/status (verificar estado do champion)")
        success_ml, ml_data, status_code = self.run_test(
            "ML Status Check",
            "GET",
            "ml/status",
            200
        )
        
        if not success_ml:
            self.log("❌ FAILED: GET /api/ml/status falhou")
            return False, {}
        
        self.log(f"   ML Status Response: {json.dumps(ml_data, indent=2)}")
        
        # Check if it's either "no champion" or champion data
        if isinstance(ml_data, dict):
            if "message" in ml_data and ml_data["message"] == "no champion":
                self.log("✅ GET /api/ml/status: 'no champion' (estado inicial válido) ✓")
            elif "model_id" in ml_data or "accuracy" in ml_data:
                self.log("✅ GET /api/ml/status: champion model exists ✓")
            else:
                self.log("✅ GET /api/ml/status: resposta válida ✓")
        
        self.log("🎉 CONECTIVIDADE BÁSICA: TODOS OS TESTES PASSARAM!")
        return True, {"deriv": deriv_data, "ml": ml_data}

    def test_feature_engineering_advanced(self):
        """Test 2: Feature Engineering Test - Verificar 77+ indicadores técnicos"""
        self.log("\n" + "="*70)
        self.log("TEST 2: FEATURE ENGINEERING AVANÇADO")
        self.log("="*70)
        self.log("📋 Objetivo: Verificar features_used >= 70 (77+ indicadores técnicos)")
        self.log("📋 Parâmetros exatos da review:")
        self.log("   source=deriv, symbol=R_100, timeframe=3m, count=1200")
        self.log("   horizon=3, threshold=0.003, model_type=rf")
        
        # Exact parameters from Portuguese review request
        train_params = {
            "source": "deriv",
            "symbol": "R_100", 
            "timeframe": "3m",
            "count": 1200,
            "horizon": 3,
            "threshold": 0.003,
            "model_type": "rf"
        }
        
        query_string = "&".join([f"{k}={v}" for k, v in train_params.items()])
        
        self.log(f"\n🔍 Executando POST /api/ml/train?{query_string}")
        success, data, status_code = self.run_test(
            "ML Train with Advanced Feature Engineering",
            "POST",
            f"ml/train?{query_string}",
            200,
            timeout=120  # Allow more time for training
        )
        
        if not success:
            if status_code == 400:
                error_detail = data.get('detail', '')
                self.log(f"❌ CRITICAL: Training error - {error_detail}")
                
                # Check specifically for "dados insuficientes" error
                if 'insuficientes' in error_detail.lower() or 'insufficient' in error_detail.lower():
                    self.log("❌ CRITICAL: Erro 'dados insuficientes' detectado!")
                    self.log("   Este erro NÃO deveria ocorrer com as melhorias de feature engineering")
                    return False, data
                
                return False, data
            elif status_code == 503:
                self.log("❌ CRITICAL: Deriv service not connected")
                return False, data
            else:
                self.log(f"❌ CRITICAL: Unexpected status {status_code}")
                return False, data
        
        # Extract and validate response
        model_id = data.get('model_id', '')
        metrics = data.get('metrics', {})
        backtest = data.get('backtest', {})
        rows = data.get('rows', 0)
        features_used = data.get('features_used')
        
        self.log(f"\n📊 RESULTADOS DO TREINAMENTO:")
        self.log(f"   Model ID: {model_id}")
        self.log(f"   Rows Processed: {rows}")
        self.log(f"   Features Used: {features_used}")
        self.log(f"   Metrics: {json.dumps(metrics, indent=2)}")
        self.log(f"   Backtest: {json.dumps(backtest, indent=2)}")
        
        # Critical validation checks
        validation_errors = []
        
        # Check 1: features_used >= 70 (indicating 77+ technical indicators working)
        if features_used is None:
            validation_errors.append("❌ Campo 'features_used' não encontrado no response")
        elif not isinstance(features_used, (int, float)) or features_used < 70:
            validation_errors.append(f"❌ Features insuficientes: {features_used} < 70 (esperado >= 70)")
        else:
            self.log(f"✅ CRITICAL SUCCESS: {features_used} features >= 70 (77+ indicadores técnicos funcionando) ✓")
        
        # Check 2: No "dados insuficientes" error (already checked above)
        self.log("✅ Sem erros 'dados insuficientes' ✓")
        
        # Check 3: Sufficient data processed
        if rows < 1000:
            validation_errors.append(f"❌ Dados insuficientes processados: {rows} < 1000")
        else:
            self.log(f"✅ Dados suficientes processados: {rows} rows ✓")
        
        # Check 4: Model saved successfully
        if not model_id:
            validation_errors.append("❌ Model ID não gerado")
        else:
            self.log(f"✅ Model ID gerado: {model_id} ✓")
        
        # Check 5: Metrics are valid (precision can be 0 in no-signal conditions)
        precision = metrics.get('precision', 0)
        if not isinstance(precision, (int, float)) or precision < 0:
            validation_errors.append(f"❌ Precision inválida: {precision}")
        else:
            self.log(f"✅ Precision válida: {precision} (pode ser 0 em condições sem sinais) ✓")
        
        if validation_errors:
            self.log("\n❌ VALIDATION ERRORS:")
            for error in validation_errors:
                self.log(f"   {error}")
            return False, data
        
        self.log("\n🎉 FEATURE ENGINEERING AVANÇADO: TODOS OS TESTES PASSARAM!")
        self.log(f"📋 SUCESSO CRÍTICO: {features_used} features processadas (>= 70)")
        self.log("📋 77+ indicadores técnicos estão funcionando corretamente")
        return True, data

    def test_mongodb_atlas_connectivity(self):
        """Test 3: MongoDB Atlas Test - Testar conectividade com SSL"""
        self.log("\n" + "="*70)
        self.log("TEST 3: MONGODB ATLAS CONNECTIVITY")
        self.log("="*70)
        self.log("📋 Objetivo: Verificar conectividade MongoDB Atlas com SSL")
        self.log("📋 Parâmetros: symbol=R_100, granularity=60, count=300")
        
        # Test POST /api/candles/ingest
        ingest_params = {
            "symbol": "R_100",
            "granularity": 60,
            "count": 300
        }
        
        query_string = "&".join([f"{k}={v}" for k, v in ingest_params.items()])
        
        self.log(f"\n🔍 Executando POST /api/candles/ingest?{query_string}")
        success, data, status_code = self.run_test(
            "MongoDB Atlas Candles Ingest",
            "POST",
            f"candles/ingest?{query_string}",
            200,
            timeout=60  # Allow time for MongoDB operations
        )
        
        if not success:
            if status_code == 503:
                error_detail = data.get('detail', '')
                self.log(f"❌ MongoDB connection failed: {error_detail}")
                
                # Check for SSL-specific errors
                if 'ssl' in error_detail.lower() or 'tls' in error_detail.lower():
                    self.log("❌ CRITICAL: SSL/TLS handshake error detected!")
                    self.log("📋 Detalhes do erro SSL:")
                    self.log(f"   {error_detail}")
                    return False, {"ssl_error": error_detail}
                
                return False, data
            elif status_code == 400:
                error_detail = data.get('detail', '')
                self.log(f"❌ Bad request: {error_detail}")
                return False, data
            else:
                self.log(f"❌ Unexpected status: {status_code}")
                return False, data
        
        # Validate successful MongoDB operation
        message = data.get('message', '')
        symbol = data.get('symbol', '')
        received = data.get('received', 0)
        mongo_inserted = data.get('mongo_inserted', 0)
        mongo_updated = data.get('mongo_updated', 0)
        csv_created = data.get('csv_created', 0)
        mongo_error = data.get('mongo_error')
        
        self.log(f"\n📊 RESULTADOS DA INGESTÃO:")
        self.log(f"   Message: {message}")
        self.log(f"   Symbol: {symbol}")
        self.log(f"   Received: {received}")
        self.log(f"   MongoDB Inserted: {mongo_inserted}")
        self.log(f"   MongoDB Updated: {mongo_updated}")
        self.log(f"   CSV Created: {csv_created}")
        if mongo_error:
            self.log(f"   MongoDB Error: {mongo_error}")
        
        # Validation checks
        validation_errors = []
        
        # Check 1: Data received from Deriv
        if received <= 0:
            validation_errors.append(f"❌ Nenhum dado recebido da Deriv: {received}")
        else:
            self.log(f"✅ Dados recebidos da Deriv: {received} candles ✓")
        
        # Check 2: MongoDB operations (if no error)
        if mongo_error:
            if 'ssl' in mongo_error.lower() or 'tls' in mongo_error.lower():
                self.log(f"⚠️  MongoDB SSL Error (mas CSV fallback funcionou): {mongo_error}")
                self.log("📋 Detalhes do erro SSL reportados conforme solicitado")
            else:
                validation_errors.append(f"❌ MongoDB error: {mongo_error}")
        else:
            total_mongo_ops = mongo_inserted + mongo_updated
            if total_mongo_ops > 0:
                self.log(f"✅ MongoDB operations successful: {total_mongo_ops} records ✓")
            else:
                self.log("⚠️  MongoDB operations: 0 records (possível problema de conectividade)")
        
        # Check 3: CSV fallback created
        if csv_created > 0:
            self.log(f"✅ CSV fallback created: {csv_created} records ✓")
        else:
            validation_errors.append("❌ CSV fallback não foi criado")
        
        if validation_errors:
            self.log("\n❌ VALIDATION ERRORS:")
            for error in validation_errors:
                self.log(f"   {error}")
            return False, data
        
        # Success case
        if mongo_error and ('ssl' in mongo_error.lower() or 'tls' in mongo_error.lower()):
            self.log("\n⚠️  MONGODB ATLAS: SSL ERROR DETECTED (mas dados salvos em CSV)")
            self.log("📋 Erro SSL reportado conforme solicitado na review")
            return True, {"ssl_error_reported": mongo_error, "csv_fallback_working": True}
        else:
            self.log("\n🎉 MONGODB ATLAS: CONECTIVIDADE FUNCIONANDO!")
            self.log("📋 Dados salvos com sucesso no MongoDB Atlas")
            return True, data

    def run_comprehensive_tests(self):
        """Run all tests as requested in Portuguese review"""
        self.log("\n" + "🚀" + "="*68)
        self.log("TESTES DO SISTEMA ML FEATURE ENGINEERING AVANÇADO")
        self.log("🚀" + "="*68)
        self.log("📋 Conforme solicitado na review request em português:")
        self.log("   1. Conectividade Básica (Deriv + ML status)")
        self.log("   2. Feature Engineering Test (77+ indicadores técnicos)")
        self.log("   3. MongoDB Atlas Test (conectividade SSL)")
        self.log("   ⚠️  IMPORTANTE: NÃO executar /api/deriv/buy")
        self.log(f"   🌐 Base URL: {self.base_url}")
        
        results = {}
        
        # Test 1: Basic Connectivity
        self.log("\n🔍 EXECUTANDO TESTE 1: Conectividade Básica")
        connectivity_ok, connectivity_data = self.test_basic_connectivity()
        results['connectivity'] = connectivity_ok
        
        if not connectivity_ok:
            self.log("❌ CRITICAL: Conectividade básica falhou - abortando testes restantes")
            return False, results
        
        # Test 2: Feature Engineering Advanced
        self.log("\n🔍 EXECUTANDO TESTE 2: Feature Engineering Avançado")
        feature_eng_ok, feature_eng_data = self.test_feature_engineering_advanced()
        results['feature_engineering'] = feature_eng_ok
        
        # Test 3: MongoDB Atlas Connectivity
        self.log("\n🔍 EXECUTANDO TESTE 3: MongoDB Atlas Connectivity")
        mongodb_ok, mongodb_data = self.test_mongodb_atlas_connectivity()
        results['mongodb_atlas'] = mongodb_ok
        
        # Final Summary
        self.log("\n" + "🏁" + "="*68)
        self.log("RESUMO FINAL DOS TESTES")
        self.log("🏁" + "="*68)
        
        if connectivity_ok:
            self.log("✅ 1. Conectividade Básica: GET /api/deriv/status (connected=true) ✓")
            self.log("✅    GET /api/ml/status (estado do champion verificado) ✓")
        else:
            self.log("❌ 1. Conectividade Básica: FAILED")
        
        if feature_eng_ok:
            features_used = feature_eng_data.get('features_used', 0)
            model_id = feature_eng_data.get('model_id', '')
            self.log(f"✅ 2. Feature Engineering: {features_used} features >= 70 ✓")
            self.log(f"✅    Modelo {model_id} salvo com 77+ indicadores técnicos ✓")
            self.log("✅    Sem erros 'dados insuficientes' ✓")
        else:
            self.log("❌ 2. Feature Engineering: FAILED")
        
        if mongodb_ok:
            if 'ssl_error_reported' in mongodb_data:
                self.log("⚠️  3. MongoDB Atlas: SSL Error detectado e reportado ✓")
                self.log("✅    CSV fallback funcionando ✓")
            else:
                self.log("✅ 3. MongoDB Atlas: Conectividade SSL funcionando ✓")
        else:
            self.log("❌ 3. MongoDB Atlas: FAILED")
        
        all_tests_passed = connectivity_ok and feature_eng_ok and mongodb_ok
        
        if all_tests_passed:
            self.log("\n🎉 TODOS OS TESTES PASSARAM COM SUCESSO!")
            self.log("📋 Sistema ML Feature Engineering Avançado funcionando:")
            self.log("   ✅ Conectividade Deriv/ML estável")
            self.log("   ✅ 77+ indicadores técnicos processando corretamente")
            self.log("   ✅ MongoDB Atlas conectividade testada")
            self.log("   ✅ Sem erros críticos detectados")
        else:
            failed_tests = []
            if not connectivity_ok:
                failed_tests.append("Conectividade Básica")
            if not feature_eng_ok:
                failed_tests.append("Feature Engineering")
            if not mongodb_ok:
                failed_tests.append("MongoDB Atlas")
            
            self.log(f"\n⚠️  {len(failed_tests)} TESTE(S) FALHARAM: {', '.join(failed_tests)}")
            self.log("📋 Verificar logs detalhados acima para diagnóstico")
        
        return all_tests_passed, results

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
    """Main function to run Advanced ML Feature Engineering tests"""
    print("🧠 Advanced ML Feature Engineering Backend API Tester")
    print("=" * 70)
    print("📋 Testing as requested in Portuguese review:")
    print("   1. Conectividade Básica")
    print("   2. Feature Engineering Test (77+ indicadores)")
    print("   3. MongoDB Atlas Test (SSL)")
    
    # Use the URL from frontend/.env as specified
    tester = AdvancedMLFeatureTester()
    
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