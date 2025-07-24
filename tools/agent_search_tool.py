# Filename: search_tool.py
# Description: A command-line tool to perform a Google search and optionally
# fetch and parse the content from the resulting links using a headless browser.
"""
A command-line tool to perform a Google search via the Custom Search API.

This script uses Playwright to control a headless browser, making it more robust
against anti-scraping measures. It can fetch and parse the primary text content
from each result URL.

Example Usage:
    # First, ensure you have a .env file in the same directory with your
    # GOOGLE_API_KEY and SEARCH_ENGINE_ID.

    # The first time you run this, it may install necessary dependencies
    # and browser binaries for Playwright, which can take a moment.

    # To perform a search and get only the links and snippets:
    python tools/agent_search_tool.py --query "latest developments in AI"

    # To perform a search AND fetch the content from each link:
    python tools/agent_search_tool.py --query "latest developments in AI" --fetch-content
"""

import os
import json
import argparse
import sys
import subprocess
import importlib.util
import asyncio

def ensure_dependencies_installed():
    """
    Checks if required packages are installed and installs them if they are not.
    Also handles the one-time setup for Playwright browsers.
    """
    required_packages = {
        "dotenv": "python-dotenv",
        "googleapiclient": "google-api-python-client",
        "bs4": "beautifulsoup4",
        "lxml": "lxml",
        "playwright": "playwright"
    }

    for module_name, package_name in required_packages.items():
        if importlib.util.find_spec(module_name) is None:
            print(f"Required package '{package_name}' not found. Attempting to install...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
                print(f"Successfully installed '{package_name}'.")

                # If playwright was just installed, we must also install its browsers.
                if package_name == "playwright":
                    print("Installing Playwright browser binaries... (This may take a moment)")
                    subprocess.check_call([sys.executable, "-m", "playwright", "install"])
                    print("Playwright browsers installed successfully.")

            except subprocess.CalledProcessError as e:
                print(f"Error: Failed to install '{package_name}'. Please install it manually.", file=sys.stderr)
                sys.exit(1)

async def fetch_and_parse_url_async(browser, url: str) -> str:
    """
    Asynchronously fetches the content of a URL using Playwright and parses the main text.
    """
    from bs4 import BeautifulSoup

    try:
        page = await browser.new_page()
        await page.goto(url, timeout=15000, wait_until='domcontentloaded')
        html_content = await page.content()
        await page.close()

        soup = BeautifulSoup(html_content, 'lxml')
        paragraphs = soup.find_all('p')
        if not paragraphs:
            return "No paragraph content found on this page."

        content = ' '.join(p.get_text(strip=True) for p in paragraphs)
        return content
    except Exception as e:
        return f"An error occurred while fetching or parsing {url}: {e}"


def perform_google_search(api_key: str, search_engine_id: str, query: str, limit: int = 5) -> dict:
    """
    Performs a Google search using the Custom Search JSON API.
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    try:
        service = build("customsearch", "v1", developerKey=api_key)
        result = service.cse().list(q=query, cx=search_engine_id, num=limit).execute()
        formatted_results = [
            {"title": item.get("title"), "link": item.get("link"), "snippet": item.get("snippet")}
            for item in result.get("items", [])
        ]
        return {"status": "success", "query": query, "results": formatted_results}
    except HttpError as e:
        return {"status": "error", "message": f"An API error occurred: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"An unexpected error occurred: {e}"}

async def main():
    """
    Main asynchronous function to parse arguments and run the tool.
    """
    ensure_dependencies_installed()

    # These imports must happen after the dependency check
    from dotenv import load_dotenv
    from playwright.async_api import async_playwright

    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    search_engine_id = os.getenv("SEARCH_ENGINE_ID")

    if not api_key or not search_engine_id:
        error_message = {"status": "error", "message": "Missing credentials."}
        print(json.dumps(error_message, indent=2))
        return

    parser = argparse.ArgumentParser(description="A command-line tool to query Google and fetch content.")
    parser.add_argument("--query", type=str, required=True, help="The search query.")
    parser.add_argument("--limit", type=int, default=3, help="Max number of results.")
    parser.add_argument("--fetch-content", action="store_true", help="Fetch content from each link.")
    args = parser.parse_args()

    search_data = perform_google_search(api_key, search_engine_id, args.query, args.limit)

    if args.fetch_content and search_data.get("status") == "success":
        print("Fetching content for search results using headless browser...", file=sys.stderr)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            tasks = []
            for result in search_data["results"]:
                link = result.get("link")
                if link:
                    print(f"  -> Queuing parse for: {link}", file=sys.stderr)
                    tasks.append(fetch_and_parse_url_async(browser, link))

            # Run all fetching tasks concurrently
            contents = await asyncio.gather(*tasks)

            # Assign the fetched content back to the results
            for i, result in enumerate(search_data["results"]):
                 if result.get("link"):
                    result["content"] = contents[i]

            await browser.close()
        print("Content fetching complete.", file=sys.stderr)

    print(json.dumps(search_data, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
