# app/services/search_service.py
# VERSION 2.0.0 -- STABLE

import os
import asyncio
import logging
import httpx  # For downloading files like PDFs
import fitz   # PyMuPDF for parsing PDFs
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# --- Configuration ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")
logger = logging.getLogger(__name__)

# --- Service Functions ---

def perform_google_search(query: str, limit: int = 5) -> list:
    """Performs a Google search and returns a list of results."""
    print(f"Executing Google Custom Search for query: '{query}'...")
    logger.info("Search Service: Executing Google Search API call.")
    try:
        service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY, cache_discovery=False)
        result = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=limit).execute()
        search_items = result.get("items", [])
        print(f"Google Search complete. Found {len(search_items)} results.")
        logger.info(f"Search Service: Found {len(search_items)} results from Google Search.")
        return search_items
    except Exception as e:
        print(f"An error occurred during Google Search: {e}")
        logger.error(f"Search Service: Error during Google Search API call: {e}")
        return []

# PROPOSED REVISION for app/services/search_service.py
async def fetch_and_parse_pdf_async(url: str, index: int) -> str:
    """
    Fetches a PDF from a URL and extracts its text content.
    Uses httpx for downloading and PyMuPDF for parsing.
    """
    task_id = f"Fetcher Task {index+1}"
    print(f"  -> [{task_id}] PDF DETECTED. Starting PDF download from: {url}")
    logger.info(f"  -> {task_id}: PDF detected. Starting to fetch URL: {url}")
    try:
        # Define more comprehensive headers to better mimic a real browser request.
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        # Use httpx with our enhanced headers and follow redirects.
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30.0) as client:
            print(f"  -> [{task_id}] Sending GET request with enhanced headers...")
            response = await client.get(url)
            # Raise an exception for bad status codes (4xx or 5xx)
            response.raise_for_status()
        
        print(f"  -> [{task_id}] PDF download complete. Parsing content...")
        pdf_bytes = response.content
        
        # Open PDF from memory and extract text
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            content = "".join(page.get_text() for page in doc)

        if content:
            print(f"  -> [{task_id}] Successfully extracted text from PDF: {url}")
            logger.info(f"  -> {task_id}: Successfully parsed PDF content from {url}")
            return content
        else:
            print(f"  -> [{task_id}] No text content was found in PDF at {url}.")
            logger.warning(f"  -> {task_id}: No text content found in PDF at {url}")
            return "No text content found in PDF."
            
    except Exception as e:
        print(f"  -> [{task_id}] AN ERROR OCCURRED while processing PDF {url}: {e}")
        logger.error(f"  -> {task_id}: Error fetching or parsing PDF {url}: {e}")
        return f"Error fetching or parsing PDF URL: {e}"

# PROPOSED REVISION for app/services/search_service.py

async def fetch_and_parse_html_async(browser, url: str, index: int) -> str:
    """Fetches and parses the main text content of an HTML URL using Playwright."""
    task_id = f"Fetcher Task {index+1}"
    print(f"  -> [{task_id}] HTML DETECTED. Starting job for URL: {url}")
    logger.info(f"  -> {task_id}: Starting to fetch HTML URL: {url}")
    try:
        page = await browser.new_page()
        print(f"  -> [{task_id}] New browser page created.")
        
        await page.goto(url, timeout=20000, wait_until='domcontentloaded')
        print(f"  -> [{task_id}] Successfully navigated to page. Retrieving content...")
        
        html_content = await page.content()
        await page.close()
        print(f"  -> [{task_id}] Page closed. Parsing HTML with BeautifulSoup.")

        soup = BeautifulSoup(html_content, 'lxml')
        content = ""

        # Strategy 1: Look for semantic main content tags.
        print(f"  -> [{task_id}] Parsing Strategy 1: Searching for <article> or <main> tags.")
        main_content = soup.find('article') or soup.find('main')
        if main_content:
            content = main_content.get_text(separator=' ', strip=True)

        # Strategy 2: If no semantic tags found, look for common content div containers.
        if not content:
            print(f"  -> [{task_id}] Parsing Strategy 2: Searching for common content <div> classes.")
            # This list can be expanded over time as we encounter new site layouts.
            potential_divs = [
                soup.find('div', class_='content'),
                soup.find('div', class_='post'),
                soup.find('div', class_='article'),
                soup.find('div', class_='blog-post'),
                soup.find('div', id='content'),
                soup.find('div', id='main')
            ]
            for div in potential_divs:
                if div:
                    content = div.get_text(separator=' ', strip=True)
                    if content: break
        
        # Strategy 3: Fallback to finding all paragraph tags as a last resort.
        if not content:
            print(f"  -> [{task_id}] Parsing Strategy 3: Falling back to all <p> tags.")
            paragraphs = soup.find_all('p')
            content = ' '.join(p.get_text(strip=True) for p in paragraphs)
            
        print(f"  -> [{task_id}] Parsing complete.")
        
        if content:
            print(f"  -> [{task_id}] Successfully extracted main content from {url}.")
            logger.info(f"  -> {task_id}: Successfully parsed content from {url}")
            return content
        else:
            print(f"  -> [{task_id}] No paragraph content was found at {url}.")
            logger.warning(f"  -> {task_id}: No paragraph content found at {url}")
            return "No paragraph content found."

    except Exception as e:
        print(f"  -> [{task_id}] AN ERROR OCCURRED while processing HTML {url}: {e}")
        logger.error(f"  -> {task_id}: Error fetching or parsing {url}: {e}")
        return f"Error fetching or parsing URL: {e}"

async def search_and_fetch_content(query: str) -> dict:
    """
    Orchestrates the search and content fetching pipeline with verbose logging.
    Routes URLs to either the HTML or PDF parser.
    """
    print(f"\n--- Starting Search and Fetch for query: '{query}' ---")
    logger.info(f"Search Service: Received query '{query}'. Starting orchestration.")
    
    # Step 1: Perform the initial Google Search
    print("\n[Step 1/4] Performing Google Custom Search...")
    search_results = perform_google_search(query)
    
    if not search_results:
        print("[Step 1/4] Google Search returned no results. Ending process.")
        logger.warning("Search Service: No search results returned. Ending process.")
        return {"results": []}
    print(f"[Step 1/4] Google Search finished.")

    # Step 2: Asynchronously fetch content for each result
    print("\n[Step 2/4] Initializing parsers to fetch content.")
    logger.info("Search Service: Launching headless browser to fetch content for all results.")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        print("             Browser for HTML parsing launched successfully.")
        
        tasks = []
        print("             Creating and queuing tasks, routing by content type (HTML/PDF)...")
        logger.info("Search Service: Creating and queuing all fetcher tasks.")
        for i, item in enumerate(search_results):
            url = item['link']
            # ROUTER: Check the URL extension and append the correct parsing task.
            if url.lower().endswith('.pdf'):
                tasks.append(fetch_and_parse_pdf_async(url, i))
            else:
                tasks.append(fetch_and_parse_html_async(browser, url, i))
        
        print(f"[Step 2/4] {len(tasks)} fetching tasks have been created.")
        
        # Step 3: Run all fetching tasks concurrently
        print(f"\n[Step 3/4] Executing {len(tasks)} fetcher tasks in parallel...")
        logger.info(f"Search Service: Executing {len(tasks)} fetcher tasks concurrently...")
        contents = await asyncio.gather(*tasks)
        print("[Step 3/4] All content fetching tasks have completed.")
        logger.info("Search Service: All fetcher tasks have completed.")
        
        await browser.close()
        print("             Browser closed.")

    # Step 4: Combine search results with their fetched content
    print("\n[Step 4/4] Combining search result metadata with the fetched content...")
    logger.info("Search Service: Combining search data with fetched content.")
    for i, item in enumerate(search_results):
        item['content'] = contents[i]
    print("[Step 4/4] Combination complete.")
        
    print("\n--- Search and Fetch process finished successfully. ---")
    logger.info("Search Service: Orchestration complete. Returning final results.")
    return {"results": search_results}