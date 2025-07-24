
import requests
import os

def test_search_endpoint():
    """
    Tests the /search endpoint of the Flask application.
    """
    try:
        with open('.port', 'r') as f:
            port = f.read().strip()
        
        url = f"http://127.0.0.1:{port}/search"
        payload = {"query": "What is the capital of France?"}

        response = requests.post(url, json=payload)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        print("Response from /search endpoint:")
        print(response.json())

    except FileNotFoundError:
        print("Error: .port file not found. Please run the server first.")
    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        print("Please ensure the Flask application is running.")

if __name__ == "__main__":
    test_search_endpoint()
