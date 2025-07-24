
import requests

def test_search_endpoint():
    """
    Tests the /search endpoint of the Flask application.
    """
    url = "http://127.0.0.1:9002/search"  # Corrected port to match run_test_server.sh
    payload = {"query": "What is the capital of France?"}

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        print("Response from /search endpoint:")
        print(response.json())

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        print("Please ensure the Flask application is running.")

if __name__ == "__main__":
    test_search_endpoint()
