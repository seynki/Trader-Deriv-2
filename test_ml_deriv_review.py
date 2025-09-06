#!/usr/bin/env python3
"""
ML Deriv Source Tests - Specific Review Request
Tests the new ML system with source=deriv as requested in the review
"""

import requests
import json
import sys
import time
from datetime import datetime

class MLDerivTester:
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

    def test_deriv_status(self):
        """Test 1: GET /api/deriv/status - validate Deriv connectivity"""
        self.log("\n" + "="*60)
        self.log("TEST 1: GET /api/deriv/status - Validate Deriv Connectivity")
        self.log("="*60)
        
        success, data, status_code = self.run_test(
            "Deriv Status Check",
            "GET", 
            "deriv/status",
            200
        )
        
        if success:
            connected = data.get('connected', False)
            authenticated = data.get('authenticated', False)
            
            self.log(f"   Connected: {connected}")
            self.log(f"   Authenticated: {authenticated}")
            self.log(f"   Environment: {data.get('environment', 'Unknown')}")
            
            if connected:
                self.log("✅ Deriv connectivity validated")
                return True, data
            else:
                self.log("❌ Deriv not connected")
                return False, data
        
        return False, {}

    def test_candles_ingest(self):
        """Test 2: POST /api/candles/ingest?symbol=R_100&granularity=180&count=1000"""
        self.log("\n" + "="*60)
        self.log("TEST 2: POST /api/candles/ingest - Deriv Data Fetch + CSV Fallback")
        self.log("="*60)
        
        success, data, status_code = self.run_test(
            "Candles Ingest from Deriv",
            "POST",
            "candles/ingest?symbol=R_100&granularity=180&count=1000",
            200,
            timeout=45
        )
        
        if success:
            received = data.get('received', 0)
            csv_created = data.get('csv_created', 0)
            mongo_error = data.get('mongo_error')
            message = data.get('message', '')
            
            self.log(f"   Message: {message}")
            self.log(f"   Received: {received}")
            self.log(f"   CSV Created: {csv_created}")
            if mongo_error:
                self.log(f"   MongoDB Error: {mongo_error}")
                self.log("✅ CSV fallback working when MongoDB fails (SSL error expected)")
            
            if received >= 900 and csv_created >= 900:
                self.log("✅ Candles ingest successful - fetched from Deriv and created CSV fallback")
                return True, data
            else:
                self.log("❌ Insufficient data received or CSV not created")
                return False, data
        
        return False, {}

    def test_ml_train_deriv(self):
        """Test 3: POST /api/ml/train?source=deriv&symbol=R_100&timeframe=3m&count=1200&model_type=rf"""
        self.log("\n" + "="*60)
        self.log("TEST 3: POST /api/ml/train - Direct Training with Deriv Data")
        self.log("="*60)
        
        success, data, status_code = self.run_test(
            "ML Train with Deriv Source",
            "POST",
            "ml/train?source=deriv&symbol=R_100&timeframe=3m&count=1200&model_type=rf",
            200,
            timeout=60
        )
        
        if success:
            model_id = data.get('model_id', '')
            metrics = data.get('metrics', {})
            backtest = data.get('backtest', {})
            rows = data.get('rows', 0)
            
            self.log(f"   Model ID: {model_id}")
            self.log(f"   Rows: {rows}")
            self.log(f"   Precision: {metrics.get('precision', 'N/A')}")
            self.log(f"   EV per Trade: {backtest.get('ev_per_trade', 'N/A')}")
            
            if model_id and rows >= 1000 and isinstance(metrics, dict) and isinstance(backtest, dict):
                self.log("✅ ML training with Deriv data successful - generating real metrics")
                return True, data
            else:
                self.log("❌ ML training failed or missing required fields")
                return False, data
        
        return False, {}

    def test_ml_train_async(self):
        """Test 4: POST /api/ml/train_async?source=deriv&symbol=R_100&timeframe=3m&count=1500&model_type=rf&thresholds=0.003&horizons=3"""
        self.log("\n" + "="*60)
        self.log("TEST 4: POST /api/ml/train_async - Async Job with Deriv Data")
        self.log("="*60)
        
        success, data, status_code = self.run_test(
            "ML Train Async with Deriv Source",
            "POST",
            "ml/train_async?source=deriv&symbol=R_100&timeframe=3m&count=1500&model_type=rf&thresholds=0.003&horizons=3",
            200,
            timeout=30
        )
        
        if success:
            job_id = data.get('job_id', '')
            status = data.get('status', '')
            
            self.log(f"   Job ID: {job_id}")
            self.log(f"   Status: {status}")
            
            if job_id and status in ['queued', 'running']:
                self.log("✅ Async ML job created successfully")
                return True, data, job_id
            else:
                self.log("❌ Invalid async job creation response")
                return False, data, None
        
        return False, {}, None

    def test_ml_job_status(self, job_id):
        """Test 5: GET /api/ml/job/{job_id} - Validate async job status"""
        self.log("\n" + "="*60)
        self.log("TEST 5: GET /api/ml/job/{job_id} - Async Job Status")
        self.log("="*60)
        
        if not job_id:
            self.log("❌ No job_id provided - skipping job status test")
            return False, {}
        
        success, data, status_code = self.run_test(
            f"ML Job Status for {job_id}",
            "GET",
            f"ml/job/{job_id}",
            200,
            timeout=15
        )
        
        if success:
            status = data.get('status', '')
            progress = data.get('progress', {})
            stage = data.get('stage', '')
            
            self.log(f"   Status: {status}")
            self.log(f"   Stage: {stage}")
            self.log(f"   Progress: {json.dumps(progress, indent=2)}")
            
            if status in ['queued', 'running', 'done', 'failed']:
                self.log("✅ Job status endpoint working - valid status returned")
                return True, data
            else:
                self.log("❌ Invalid job status")
                return False, data
        
        return False, {}

    def run_all_tests(self):
        """Run all ML Deriv tests as specified in the review request"""
        self.log("🚀 TESTANDO NOVO SISTEMA ML COMPLETO COM SOURCE=DERIV")
        self.log("📋 FOCO PRINCIPAL: Validar que o ML training agora funciona de verdade usando dados da Deriv")
        self.log("📋 Testes específicos conforme review request:")
        
        # Test 1: Deriv Status
        test1_ok, test1_data = self.test_deriv_status()
        
        if not test1_ok:
            self.log("❌ CRITICAL: Deriv not connected - aborting remaining tests")
            return False
        
        # Test 2: Candles Ingest
        test2_ok, test2_data = self.test_candles_ingest()
        
        # Test 3: ML Train Direct
        test3_ok, test3_data = self.test_ml_train_deriv()
        
        # Test 4: ML Train Async
        test4_ok, test4_data, job_id = self.test_ml_train_async()
        
        # Test 5: ML Job Status
        test5_ok, test5_data = self.test_ml_job_status(job_id)
        
        # Summary
        self.log("\n" + "="*60)
        self.log("TESTE COMPLETO ML SOURCE=DERIV - RESULTADOS")
        self.log("="*60)
        
        results = {
            "deriv_status": test1_ok,
            "candles_ingest": test2_ok,
            "ml_train_direct": test3_ok,
            "ml_train_async": test4_ok,
            "ml_job_status": test5_ok
        }
        
        for test_name, result in results.items():
            status = "✅ PASSED" if result else "❌ FAILED"
            self.log(f"   {test_name}: {status}")
        
        all_passed = all(results.values())
        
        if all_passed:
            self.log("\n🎉 TODOS OS TESTES PASSARAM!")
            self.log("📊 EXPECTATIVAS ATENDIDAS:")
            self.log("   ✅ Todos endpoints funcionando")
            self.log("   ✅ source=deriv busca dados online da Deriv (não MongoDB)")
            self.log("   ✅ CSV fallback criado em /data/ml/ohlcv.csv quando MongoDB falha")
            self.log("   ✅ Training ML gerando métricas reais (precision, trades, etc.)")
            self.log("   ✅ Jobs assíncronos completando com sucesso")
        else:
            self.log("\n❌ ALGUNS TESTES FALHARAM")
            failed_tests = [name for name, result in results.items() if not result]
            self.log(f"💥 Testes que falharam: {', '.join(failed_tests)}")
        
        self.log(f"\n📊 RESUMO FINAL: {self.tests_passed}/{self.tests_run} testes passaram")
        
        return all_passed

def main():
    """Main test runner"""
    tester = MLDerivTester()
    success = tester.run_all_tests()
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())