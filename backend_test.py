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
    def __init__(self, base_url="https://deriv-trading-2.preview.emergentagent.com"):
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

    def test_deriv_proposal(self):
        """Test 3: POST /api/deriv/proposal - Get pricing for R_100 CALL"""
        self.log("\n" + "="*60)
        self.log("TEST 3: Deriv Proposal Request")
        self.log("="*60)
        
        proposal_data = {
            "symbol": "R_100",
            "contract_type": "CALL",
            "duration": 5,
            "duration_unit": "t",
            "stake": 1,
            "currency": "USD"
        }
        
        success, data, status_code = self.run_test(
            "Deriv Proposal (R_100 CALL, 5 ticks, $1)",
            "POST",
            "deriv/proposal", 
            200,
            proposal_data,
            timeout=20
        )
        
        if success:
            ask_price = data.get('ask_price', 0)
            payout = data.get('payout', 0)
            proposal_id = data.get('id')
            
            self.log(f"   Proposal ID: {proposal_id}")
            self.log(f"   Symbol: {data.get('symbol')}")
            self.log(f"   Contract Type: {data.get('contract_type')}")
            self.log(f"   Ask Price: ${ask_price}")
            self.log(f"   Payout: ${payout}")
            self.log(f"   Spot: {data.get('spot')}")
            
            if proposal_id and ask_price > 0 and payout > 0:
                self.log("✅ Proposal looks valid (has id, ask_price > 0, payout > 0)")
                return True, data
            else:
                self.log("⚠️  Proposal validation failed")
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
        """Run all backend API tests"""
        self.log("🚀 Starting Deriv Backend API Tests")
        self.log(f"   Base URL: {self.base_url}")
        self.log(f"   API URL: {self.api_url}")
        self.log(f"   Timestamp: {datetime.now().isoformat()}")
        
        # Test 0: Basic health
        basic_ok = self.test_basic_endpoints()
        
        # Test 1: Deriv status
        status_ok = self.test_deriv_status()
        
        if not status_ok:
            self.log("\n❌ CRITICAL: Deriv connection failed. Stopping further tests.")
            self.print_summary()
            return False
        
        # Test 2: Proposal
        proposal_ok, proposal_data = self.test_deriv_proposal()
        
        if not proposal_ok:
            self.log("\n❌ CRITICAL: Proposal failed. Stopping buy test.")
            self.print_summary()
            return False
        
        # Test 3: Buy (only if proposal worked)
        buy_ok = self.test_deriv_buy()
        
        self.print_summary()
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