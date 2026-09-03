#!/usr/bin/env python3

import requests
import json

# Test the API endpoints

base_url = "http://localhost:8000"

def test_get_request():
    """Test GET request with comma-separated values"""
    print("Testing GET request with comma-separated values...")
    url = f"{base_url}/api/tools/array-sum/?values=1,2,3,4,5"
    try:
        response = requests.get(url)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print(f"Response: {response.json()}")
        else:
            print(f"Error Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

def test_get_multiple_values():
    """Test GET request with multiple values parameters"""
    print("\nTesting GET request with multiple values...")
    url = f"{base_url}/api/tools/array-sum/?values=1&values=2&values=3&values=4&values=5"
    try:
        response = requests.get(url)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print(f"Response: {response.json()}")
        else:
            print(f"Error Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

def test_get_json_array():
    """Test GET request with JSON array"""
    print("\nTesting GET request with JSON array...")
    url = f"{base_url}/api/tools/array-sum/?array=[1,2,3,4,5]"
    try:
        response = requests.get(url)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print(f"Response: {response.json()}")
        else:   
            print(f"Error Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

def test_post_request():
    """Test POST request (existing functionality)"""
    print("\nTesting POST request...")
    url = f"{base_url}/api/tools/array-sum/"
    data = {
        "input_data": {
            "array": [1, 2, 3, 4, 5]
        }
    }
    try:
        response = requests.post(url, json=data)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print(f"Response: {response.json()}")
        else:
            print(f"Error Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    print("Testing Array Sum API endpoints...")
    print("=" * 50)

    test_get_request()
    test_get_multiple_values()
    test_get_json_array()
    test_post_request()

    print("\n" + "=" * 50)
    print("Testing completed!")