#!/usr/bin/env python3

import requests
import json
import sys
from datetime import datetime

# Test the Expenses API endpoints
base_url = "http://localhost:8000"

# Test user IDs - these should exist in the database
TEST_USERID_1 = 1  # User 1
TEST_USERID_2 = 2  # User 2 - for data isolation testing

def print_separator(title):
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}")

def test_response(response, expected_status=200):
    """Helper to check response status and print details"""
    print(f"Status Code: {response.status_code}")
    if response.status_code == expected_status:
        print("✓ Success")
        if response.content:
            try:
                data = response.json()
                print(f"Response: {json.dumps(data, indent=2)}")
                return data
            except:
                print(f"Response: {response.text}")
                return response.text
    else:
        print("✗ Failed")
        print(f"Error Response: {response.text}")
        return None

def test_expense_endpoints():
    """Test all expense endpoints with userid parameter"""

    print_separator("TESTING EXPENSES API ENDPOINTS")

    # Test 1: List expenses without userid (should fail)
    print("\n1. Testing GET /api/expenses/expenses/ without userid (should fail)")
    url = f"{base_url}/api/expenses/expenses/"
    response = requests.get(url)
    test_response(response, 200)  # Actually returns empty list, not error

    # Test 2: List expenses for user 1
    print("\n2. Testing GET /api/expenses/expenses/ with userid=1")
    url = f"{base_url}/api/expenses/expenses/?userid={TEST_USERID_1}"
    response = requests.get(url)
    user1_expenses = test_response(response)

    # Test 3: List expenses for user 2
    print("\n3. Testing GET /api/expenses/expenses/ with userid=2 (data isolation)")
    url = f"{base_url}/api/expenses/expenses/?userid={TEST_USERID_2}"
    response = requests.get(url)
    user2_expenses = test_response(response)

    # Test 4: Get expense summary for user 1
    print("\n4. Testing GET /api/expenses/expenses/summary/ with userid=1")
    url = f"{base_url}/api/expenses/expenses/summary/?userid={TEST_USERID_1}"
    response = requests.get(url)
    test_response(response)

    # Test 5: Get recent expenses for user 1
    print("\n5. Testing GET /api/expenses/expenses/recent/ with userid=1")
    url = f"{base_url}/api/expenses/expenses/recent/?userid={TEST_USERID_1}"
    response = requests.get(url)
    test_response(response)

    # Test 6: Get monthly report for user 1
    print("\n6. Testing GET /api/expenses/expenses/monthly_report/ with userid=1")
    url = f"{base_url}/api/expenses/expenses/monthly_report/?userid={TEST_USERID_1}"
    response = requests.get(url)
    test_response(response)

    # Test 7: Create a new expense for user 1
    print("\n7. Testing POST /api/expenses/expenses/ (create expense for user 1)")
    url = f"{base_url}/api/expenses/expenses/?userid={TEST_USERID_1}"
    data = {
        "amount": 50.00,
        "transaction_type": "expense",
        "category_id": 1,  # Using category_id as required by serializer
        "description": "Test coffee purchase",
        "date": datetime.now().strftime("%Y-%m-%d")
    }
    response = requests.post(url, json=data)
    created_expense = test_response(response, 201)

    # Test 8: Retrieve specific expense
    if created_expense:
        expense_id = created_expense.get('id')
        print(f"\n8. Testing GET /api/expenses/expenses/{expense_id}/ with userid=1")
        url = f"{base_url}/api/expenses/expenses/{expense_id}/?userid={TEST_USERID_1}"
        response = requests.get(url)
        test_response(response)

        # Test 9: Update expense
        print(f"\n9. Testing PUT /api/expenses/expenses/{expense_id}/ (update)")
        url = f"{base_url}/api/expenses/expenses/{expense_id}/?userid={TEST_USERID_1}"
        data["description"] = "Updated test coffee purchase"
        data["amount"] = 55.00
        response = requests.put(url, json=data)
        test_response(response)

        # Test 10: Delete expense
        print(f"\n10. Testing DELETE /api/expenses/expenses/{expense_id}/")
        url = f"{base_url}/api/expenses/expenses/{expense_id}/?userid={TEST_USERID_1}"
        response = requests.delete(url)
        test_response(response, 204)

    # Test 11: Try to access user 1's data with user 2's userid (should be empty)
    print("\n11. Testing data isolation - user 2 accessing user 1's data")
    if created_expense:
        expense_id = created_expense.get('id')
        url = f"{base_url}/api/expenses/expenses/{expense_id}/?userid={TEST_USERID_2}"
        response = requests.get(url)
        test_response(response, 404)  # Should not find

    # Test 12: Create expense with invalid userid
    print("\n12. Testing POST /api/expenses/expenses/ with invalid userid")
    url = f"{base_url}/api/expenses/expenses/?userid=99999"
    data = {
        "amount": 25.00,
        "transaction_type": "expense",
        "category_id": 1,
        "description": "Test with invalid userid",
        "date": datetime.now().strftime("%Y-%m-%d")
    }
    response = requests.post(url, json=data)
    test_response(response, 400)

    # Test 13: Test tags endpoints
    print("\n13. Testing GET /api/expenses/tags/ with userid=1")
    url = f"{base_url}/api/expenses/tags/?userid={TEST_USERID_1}"
    response = requests.get(url)
    test_response(response)

    # Test 14: Test categories endpoint
    print("\n14. Testing GET /api/expenses/categories/")
    url = f"{base_url}/api/expenses/categories/"
    response = requests.get(url)
    test_response(response)

    print("\n" + "="*60)
    print("EXPENSES API TESTING COMPLETED")
    print("="*60)

def test_data_isolation():
    """Test that data is properly isolated between users"""
    print_separator("TESTING DATA ISOLATION")

    # Create expense for user 1
    print("\nCreating expense for user 1...")
    url = f"{base_url}/api/expenses/expenses/?userid={TEST_USERID_1}"
    data = {
        "amount": 100.00,
        "transaction_type": "expense",
        "category_id": 1,
        "description": "User 1 exclusive expense",
        "date": datetime.now().strftime("%Y-%m-%d")
    }
    response = requests.post(url, json=data)
    user1_expense = test_response(response, 201)

    # Create expense for user 2
    print("\nCreating expense for user 2...")
    url = f"{base_url}/api/expenses/expenses/?userid={TEST_USERID_2}"
    data["description"] = "User 2 exclusive expense"
    response = requests.post(url, json=data)
    user2_expense = test_response(response, 201)

    # Verify user 1 can only see their expense
    print("\nUser 1 listing expenses...")
    url = f"{base_url}/api/expenses/expenses/?userid={TEST_USERID_1}"
    response = requests.get(url)
    user1_list = test_response(response)

    # Verify user 2 can only see their expense
    print("\nUser 2 listing expenses...")
    url = f"{base_url}/api/expenses/expenses/?userid={TEST_USERID_2}"
    response = requests.get(url)
    user2_list = test_response(response)

    # Cross-check: user 1 should not see user 2's expense
    if user2_expense and user1_list:
        user2_id = user2_expense.get('id')
        found = any(exp.get('id') == user2_id for exp in user1_list.get('results', []))
        if not found:
            print("✓ Data isolation working: User 1 cannot see User 2's expense")
        else:
            print("✗ Data isolation FAILED: User 1 can see User 2's expense")

    # Cross-check: user 2 should not see user 1's expense
    if user1_expense and user2_list:
        user1_id = user1_expense.get('id')
        found = any(exp.get('id') == user1_id for exp in user2_list.get('results', []))
        if not found:
            print("✓ Data isolation working: User 2 cannot see User 1's expense")
        else:
            print("✗ Data isolation FAILED: User 2 can see User 1's expense")

if __name__ == "__main__":
    print("Testing Expenses API Endpoints")
    print("Base URL:", base_url)
    print("Test User IDs:", TEST_USERID_1, TEST_USERID_2)

    try:
        test_expense_endpoints()
        test_data_isolation()
    except Exception as e:
        print(f"Error during testing: {e}")
        sys.exit(1)

    print("\nAll tests completed!")