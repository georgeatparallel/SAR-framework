
import os
import asyncio
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SEARCH_ENGINE_ID = os.getenv("SEARCH_ENGINE_ID")

def perform_google_search(query: str, limit: int = 5) -> list:
    """Performs a Google search and returns a list of results."""
    try:
        service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY, cache_discovery=False)
        result = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=limit).execute()
        return result.get("items", [])
    except Exception as e:
        print(f"Error during Google Search: {e}")
        return []

async def fetch_and_parse_url_async(browser, url: str) -> str:
    """Fetches and parses the main text content of a URL using Playwright."""
    try:
        page = await browser.new_page()
        await page.goto(url, timeout=15000, wait_until='domcontentloaded')
        html_content = await page.content()
        await page.close()
        soup = BeautifulSoup(html_content, 'lxml')
        paragraphs = soup.find_all('p')
        content = ' '.join(p.get_text(strip=True) for p in paragraphs)
        return content if content else "No paragraph content found."
    except Exception as e:
        return f"Error fetching or parsing {url}: {e}"

async def search_and_fetch_content(query: str) -> dict:
    """
    Orchestrates search and content fetching.
    """
    print(f"Search Service: Received query '{query}'")
    search_results = perform_google_search(query)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        tasks = []
        for item in search_results:
            tasks.append(fetch_and_parse_url_async(browser, item['link']))
        contents = await asyncio.gather(*tasks)
        await browser.close()

    for i, item in enumerate(search_results):
        item['content'] = contents[i]
        
    return {"results": search_results}
