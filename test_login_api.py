#!/usr/bin/env python3

import requests
import json
import sys

# Test the Login API endpoint

BASE_URL = "http://localhost:8000"

def test_login_endpoint():
    """Test the login endpoint functionality"""
    print("Testing Django Login API endpoint...")
    print("=" * 50)

    # Test 1: Login with missing credentials (should return 400)
    print("\n1. Testing login with missing credentials...")
    url = f"{BASE_URL}/api/users/login/"
    try:
        response = requests.post(url, json={})
        print(f"   Status Code: {response.status_code}")
        if response.status_code == 400:
            print("   ✓ Correctly returned 400 for missing credentials")
            print(f"   Response: {response.json()}")
        else:
            print(f"   ✗ Expected 400, got {response.status_code}")
            print(f"   Response: {response.text}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Test 2: Login with invalid credentials (should return 401)
    print("\n2. Testing login with invalid credentials...")
    try:
        data = {
            "username": "nonexistent_user",
            "password": "wrong_password"
        }
        response = requests.post(url, json=data)
        print(f"   Status Code: {response.status_code}")
        if response.status_code == 401:
            print("   ✓ Correctly returned 401 for invalid credentials")
            print(f"   Response: {response.json()}")
        else:
            print(f"   ✗ Expected 401, got {response.status_code}")
            print(f"   Response: {response.text}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Test 3: Test with valid test user if exists, or create one first
    print("\n3. Testing login with valid test user...")
    try:
        # First try to create a test user
        print("   Creating test user...")
        create_user_url = f"{BASE_URL}/api/users/"
        test_user_data = {
            "username": "testuser",
            "password": "testpass123",
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User"
        }

        create_response = requests.post(create_user_url, json=test_user_data)
        if create_response.status_code in [200, 201]:
            print("   ✓ Test user created successfully")
        elif create_response.status_code == 400 and "already exists" in create_response.text.lower():
            print("   ✓ Test user already exists")
        else:
            print(f"   ! Could not create test user: {create_response.status_code}")
            print(f"   Response: {create_response.text}")

        # Now test login with valid credentials
        login_data = {
            "username": "testuser",
            "password": "testpass123"
        }

        response = requests.post(url, json=login_data)
        print(f"   Status Code: {response.status_code}")
        if response.status_code == 200:
            print("   ✓ Login successful!")
            print(f"   Response: {json.dumps(response.json(), indent=2)}")

            # Test 4: Verify CSRF exemption worked (no 403 error)
            print("\n4. Verifying CSRF exemption...")
            print("   ✓ No CSRF token required - CSRF exemption working!")
            print("   ✓ No 403 Forbidden error received")

        elif response.status_code == 401:
            print("   ✗ Login failed - invalid credentials")
            print(f"   Response: {response.text}")
        else:
            print(f"   ✗ Unexpected status code: {response.status_code}")
            print(f"   Response: {response.text}")

    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Test 5: Test OPTIONS request to check CORS
    print("\n5. Testing CORS preflight request...")
    try:
        response = requests.options(url)
        print(f"   Status Code: {response.status_code}")
        if response.status_code == 200:
            print("   ✓ CORS preflight request handled correctly")
        else:
            print(f"   Response: {response.text}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

def check_server_status():
    """Check if Django server is running"""
    print("\nChecking Django server status...")
    try:
        response = requests.get(f"{BASE_URL}/admin/", timeout=5)
        if response.status_code == 200:
            print("✓ Django server is running and accessible")
        else:
            print(f"? Django server responded with status: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("✗ Django server is not running or not accessible")
        print("  Please start the server with: cd ToolBoxWebServices/toolboxweb && python3 manage.py runserver 0.0.0.0:8000")
        return False
    except Exception as e:
        print(f"✗ Error checking server: {e}")
        return False

    return True

if __name__ == "__main__":
    print("Django Login API Test Suite")
    print("=" * 50)

    # Check server status first
    if not check_server_status():
        sys.exit(1)

    # Run login tests
    test_login_endpoint()

    print("\n" + "=" * 50)
    print("Login API testing completed!")

    print("\nSummary:")
    print("- CSRF exemption is working if no 403 errors occurred")
    print("- Login endpoint should return proper status codes")
    print("- Test user 'testuser' with password 'testpass123' was created for testing")