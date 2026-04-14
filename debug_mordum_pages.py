from playwright.sync_api import sync_playwright
import time

BASE_URL = "https://lostark.bible"

# The most recent Mordum entries that returned NO TIMESTAMP
LOG_URLS = [
    "/logs/hFjYDBo",
    "/logs/7SBmWuM",
    "/logs/vygjTCz",
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    for log_url in LOG_URLS:
        full_url = f"{BASE_URL}{log_url}"
        print(f"\n{'='*60}")
        print(f"LOG: {log_url}")
        print(f"{'='*60}")
        
        page.goto(full_url, timeout=60000)
        page.wait_for_load_state("networkidle")
        time.sleep(2)
        
        body_text = page.locator("body").inner_text()
        print(f"First 800 chars:")
        print(body_text[:800])
        print(f"\n--- END ---")
    
    browser.close()
