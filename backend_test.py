#!/usr/bin/env python3
"""
Backend API Testing for Deriv Trading Integration
Tests the FastAPI backend endpoints for Deriv WebSocket integration
"""

import requests
import json
import sys
import time
from datetime import datetime

class DerivAPITester:
    def __init__(self, base_url="https://deriv-checker.preview.emergentagent.com"):
        self.base_url = base_url
        self.api_url = f"{base_url}/api"
        self.tests_run = 0
        self.tests_passed = 0
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def log(self, message):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def run_test(self, name, method, endpoint, expected_status, data=None, timeout=15):
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
        """Test 1: GET /api/deriv/status - Check Deriv connection status"""
        self.log("\n" + "="*60)
        self.log("TEST 1: Deriv Status Check")
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
            self.log(f"   Symbols: {data.get('symbols', [])}")
            
            if connected and authenticated:
                self.log("✅ Deriv connection is healthy")
                return True
            else:
                self.log("⚠️  Deriv connection issues detected")
                return False
        
        return False

    def test_deriv_contracts_for(self):
        """Test 2: GET /api/deriv/contracts_for/R_100 - Get available contracts for R_100"""
        self.log("\n" + "="*60)
        self.log("TEST 2: Deriv Contracts For R_100")
        self.log("="*60)
        
        success, data, status_code = self.run_test(
            "Deriv Contracts For R_100",
            "GET",
            "deriv/contracts_for/R_100",
            200,
            timeout=15
        )
        
        if success:
            contract_types = data.get('contract_types', [])
            duration_units = data.get('duration_units', [])
            durations = data.get('durations', {})
            
            self.log(f"   Symbol: {data.get('symbol')}")
            self.log(f"   Contract Types: {contract_types}")
            self.log(f"   Duration Units: {duration_units}")
            self.log(f"   Durations: {json.dumps(durations, indent=4)}")
            self.log(f"   Has Barrier: {data.get('has_barrier', False)}")
            
            if contract_types and duration_units and durations:
                self.log("✅ Contracts data looks valid (has contract_types, duration_units, durations)")
                return True, data
            else:
                self.log("⚠️  Contracts data validation failed - missing required fields")
                return False, data
        
        return False, {}

    def test_deriv_contracts_for_r10_accumulator(self):
        """Test 3: GET /api/deriv/contracts_for/R_10?product_type=accumulator"""
        self.log("\n" + "="*60)
        self.log("TEST 3: Deriv Contracts For R_10 (Accumulator)")
        self.log("="*60)
        
        success, data, status_code = self.run_test(
            "Deriv Contracts For R_10 (product_type=accumulator)",
            "GET",
            "deriv/contracts_for/R_10?product_type=accumulator",
            200,
            timeout=15
        )
        
        if success:
            product_type = data.get('product_type')
            currency = data.get('currency')
            contract_types = data.get('contract_types', [])
            
            self.log(f"   Symbol: {data.get('symbol')}")
            self.log(f"   Product Type: {product_type}")
            self.log(f"   Currency: {currency}")
            self.log(f"   Contract Types: {contract_types}")
            self.log(f"   Landing Company: {data.get('landing_company')}")
            
            # Validate required fields
            valid = True
            if product_type != 'accumulator':
                self.log("❌ Product type should be 'accumulator'")
                valid = False
            if not currency:
                self.log("❌ Currency should not be empty")
                valid = False
            
            # Contract types can be empty (depends on landing_company)
            if not contract_types:
                self.log("⚠️  Contract types list is empty (acceptable, depends on landing_company)")
            
            if valid:
                self.log("✅ R_10 accumulator contracts data is valid")
                return True, data
            else:
                self.log("❌ R_10 accumulator contracts validation failed")
                return False, data
        
        return False, {}

    def test_deriv_contracts_for_smart_r10_accumulator(self):
        """Test 4: GET /api/deriv/contracts_for_smart/R_10?product_type=accumulator"""
        self.log("\n" + "="*60)
        self.log("TEST 4: Deriv Contracts For Smart R_10 (Accumulator)")
        self.log("="*60)
        
        success, data, status_code = self.run_test(
            "Deriv Contracts For Smart R_10 (product_type=accumulator)",
            "GET",
            "deriv/contracts_for_smart/R_10?product_type=accumulator",
            200,
            timeout=15
        )
        
        if success:
            tried = data.get('tried', [])
            first_supported = data.get('first_supported')
            results = data.get('results', {})
            product_type = data.get('product_type')
            
            self.log(f"   Tried: {tried}")
            self.log(f"   First Supported: {first_supported}")
            self.log(f"   Product Type: {product_type}")
            self.log(f"   Results Keys: {list(results.keys())}")
            
            # Validate required structure
            valid = True
            if not isinstance(tried, list):
                self.log("❌ 'tried' should be a list")
                valid = False
            if not isinstance(results, dict):
                self.log("❌ 'results' should be a dict")
                valid = False
            if product_type != 'accumulator':
                self.log("❌ Product type should be 'accumulator'")
                valid = False
            
            if valid:
                self.log("✅ R_10 smart accumulator contracts structure is valid")
                return True, data
            else:
                self.log("❌ R_10 smart accumulator contracts validation failed")
                return False, data
        
        return False, {}

    def test_deriv_contracts_for_r10_turbos(self):
        """Test 5: GET /api/deriv/contracts_for/R_10?product_type=turbos"""
        self.log("\n" + "="*60)
        self.log("TEST 5: Deriv Contracts For R_10 (Turbos)")
        self.log("="*60)
        
        success, data, status_code = self.run_test(
            "Deriv Contracts For R_10 (product_type=turbos)",
            "GET",
            "deriv/contracts_for/R_10?product_type=turbos",
            200,
            timeout=15
        )
        
        if success:
            product_type = data.get('product_type')
            currency = data.get('currency')
            contract_types = data.get('contract_types', [])
            
            self.log(f"   Symbol: {data.get('symbol')}")
            self.log(f"   Product Type: {product_type}")
            self.log(f"   Currency: {currency}")
            self.log(f"   Contract Types: {contract_types}")
            
            # Validate required fields
            valid = True
            if product_type != 'turbos':
                self.log("❌ Product type should be 'turbos'")
                valid = False
            if not currency:
                self.log("❌ Currency should not be empty")
                valid = False
            
            if valid:
                self.log("✅ R_10 turbos contracts data is valid")
                return True, data
            else:
                self.log("❌ R_10 turbos contracts validation failed")
                return False, data
        
        return False, {}

    def test_deriv_contracts_for_r10_multipliers(self):
        """Test 6: GET /api/deriv/contracts_for/R_10?product_type=multipliers"""
        self.log("\n" + "="*60)
        self.log("TEST 6: Deriv Contracts For R_10 (Multipliers)")
        self.log("="*60)
        
        success, data, status_code = self.run_test(
            "Deriv Contracts For R_10 (product_type=multipliers)",
            "GET",
            "deriv/contracts_for/R_10?product_type=multipliers",
            200,
            timeout=15
        )
        
        if success:
            product_type = data.get('product_type')
            currency = data.get('currency')
            contract_types = data.get('contract_types', [])
            
            self.log(f"   Symbol: {data.get('symbol')}")
            self.log(f"   Product Type: {product_type}")
            self.log(f"   Currency: {currency}")
            self.log(f"   Contract Types: {contract_types}")
            
            # Validate required fields
            valid = True
            if product_type != 'multipliers':
                self.log("❌ Product type should be 'multipliers'")
                valid = False
            if not currency:
                self.log("❌ Currency should not be empty")
                valid = False
            
            if valid:
                self.log("✅ R_10 multipliers contracts data is valid")
                return True, data
            else:
                self.log("❌ R_10 multipliers contracts validation failed")
                return False, data
        
        return False, {}



    def test_basic_endpoints(self):
        """Test basic API endpoints"""
        self.log("\n" + "="*60)
        self.log("TEST 0: Basic API Health Check")
        self.log("="*60)
        
        # Test root endpoint
        success, data, status_code = self.run_test(
            "Root API Endpoint",
            "GET",
            "",
            200
        )
        
        return success

    def run_all_tests(self):
        """Run all backend API tests - NON-INVASIVE Deriv flow only"""
        self.log("🚀 Starting Deriv Backend API Tests (NON-INVASIVE)")
        self.log(f"   Base URL: {self.base_url}")
        self.log(f"   API URL: {self.api_url}")
        self.log(f"   Timestamp: {datetime.now().isoformat()}")
        self.log("   NOTE: NOT testing /api/deriv/buy to avoid real trades")
        
        # Test 0: Basic health
        basic_ok = self.test_basic_endpoints()
        
        # Test 1: Deriv status - REQUIRED
        status_ok = self.test_deriv_status()
        
        if not status_ok:
            self.log("\n❌ CRITICAL: Deriv connection failed. Stopping further tests.")
            self.print_summary()
            return False
        
        # Test 2: Contracts for R_100 (legacy test)
        contracts_ok, contracts_data = self.test_deriv_contracts_for()
        
        # Test 3: R_10 Accumulator contracts
        r10_acc_ok, r10_acc_data = self.test_deriv_contracts_for_r10_accumulator()
        
        # Test 4: R_10 Smart Accumulator contracts
        r10_smart_acc_ok, r10_smart_acc_data = self.test_deriv_contracts_for_smart_r10_accumulator()
        
        # Test 5: R_10 Turbos contracts
        r10_turbos_ok, r10_turbos_data = self.test_deriv_contracts_for_r10_turbos()
        
        # Test 6: R_10 Multipliers contracts
        r10_mult_ok, r10_mult_data = self.test_deriv_contracts_for_r10_multipliers()
        
        self.print_summary()
        
        # Summary of key findings
        self.log("\n" + "="*60)
        self.log("KEY FINDINGS SUMMARY")
        self.log("="*60)
        
        if status_ok:
            self.log("✅ Deriv Status: Connected and authenticated")
        else:
            self.log("❌ Deriv Status: Connection issues")
            
        if r10_acc_ok:
            self.log("✅ R_10 Accumulator: Working correctly")
        else:
            self.log("❌ R_10 Accumulator: Issues detected")
            
        if r10_smart_acc_ok:
            self.log("✅ R_10 Smart Accumulator: Working correctly")
        else:
            self.log("❌ R_10 Smart Accumulator: Issues detected")
            
        if r10_turbos_ok:
            self.log("✅ R_10 Turbos: Working correctly")
        else:
            self.log("❌ R_10 Turbos: Issues detected")
            
        if r10_mult_ok:
            self.log("✅ R_10 Multipliers: Working correctly")
        else:
            self.log("❌ R_10 Multipliers: Issues detected")
        
        return self.tests_passed == self.tests_run

    def print_summary(self):
        """Print test summary"""
        self.log("\n" + "="*60)
        self.log("TEST SUMMARY")
        self.log("="*60)
        self.log(f"Tests Run: {self.tests_run}")
        self.log(f"Tests Passed: {self.tests_passed}")
        self.log(f"Tests Failed: {self.tests_run - self.tests_passed}")
        self.log(f"Success Rate: {(self.tests_passed/self.tests_run*100):.1f}%" if self.tests_run > 0 else "0%")
        
        if self.tests_passed == self.tests_run:
            self.log("🎉 ALL TESTS PASSED!")
        else:
            self.log("⚠️  SOME TESTS FAILED")

def main():
    """Main test runner"""
    tester = DerivAPITester()
    success = tester.run_all_tests()
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())