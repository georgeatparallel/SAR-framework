
import requests
import os
from app.config import logging_config
import logging

# --- 1. SETUP LOGGING ---
logging_config.setup_logging()
logger = logging.getLogger(__name__)

def test_search_endpoint():
    """
    Tests the /search endpoint of the Flask application.
    """
    try:
        with open('.port', 'r') as f:
            port = f.read().strip()
        
        url = f"http://127.0.0.1:{port}/search"
        #payload = {"query": "How many municipalities are in Region Metropolitana, Chile?"}
        payload = {"query": "What were the key arguments for and against the 'right to repair' movement in the last year, specifically comparing the perspectives of consumer electronics manufacturers and independent repair advocates?"}

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
