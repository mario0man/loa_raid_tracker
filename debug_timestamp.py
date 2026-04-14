from playwright.sync_api import sync_playwright
import re
import time

BASE_URL = "https://lostark.bible"

# A few log IDs from the roster to test
LOG_URLS = [
    "/logs/Q0jNzPm",   # Kazeros G2 - 6 hours ago
    "/logs/3ueWGS2",   # Kazeros G1 - 6 hours ago  
    "/logs/i3cW172",   # Armoche G2 - 4 days ago
    "/logs/peQnHq0",   # Armoche G1 - 4 days ago
    "/logs/BFwZqQp",   # Mordum G3 - 17 days ago
    "/logs/MWwOE1A",   # Thaemine G4 - 29 days ago
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    for log_url in LOG_URLS:
        full_url = f"{BASE_URL}{log_url}"
        print(f"\n{'='*60}")
        print(f"LOG: {log_url}")
        print(f"URL: {full_url}")
        print(f"{'='*60}")
        
        page.goto(full_url, timeout=60000)
        page.wait_for_load_state("networkidle")
        time.sleep(1)
        
        body_text = page.locator("body").inner_text()
        
        # Try the regex
        ts_match = re.search(r'(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)', body_text)
        if ts_match:
            print(f"  MATCH: '{ts_match.group(1)}'")
        else:
            print(f"  NO MATCH for timestamp regex")
        
        # Print the first 500 chars of body to see structure
        print(f"\n  First 500 chars of body text:")
        print(f"  {repr(body_text[:500])}")
        
        # Also try to find anything that looks like a date
        date_likes = re.findall(r'\d{1,2}/\d{1,2}/\d{4}.*?(?:AM|PM)', body_text)
        print(f"\n  All date-like patterns found: {date_likes}")
    
    browser.close()
