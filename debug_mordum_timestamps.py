from playwright.sync_api import sync_playwright
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

# The most recent Mordum entries from the debug
LOG_URLS = [
    ("/logs/hFjYDBo", "Mordum G3 - 2 hours ago"),
    ("/logs/7SBmWuM", "Mordum G2 - 2 hours ago"),
    ("/logs/vygjTCz", "Mordum G1 - 2 hours ago"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    now_pacific = datetime.now(PACIFIC_TZ)
    # Wednesday reset
    days_since_wed = (now_pacific.weekday() - 2) % 7
    last_wed = now_pacific - timedelta(days=days_since_wed)
    reset_time = last_wed.replace(hour=3, minute=0, second=0, microsecond=0)
    if reset_time > now_pacific:
        reset_time -= timedelta(weeks=1)
    
    print(f"Current Pacific time: {now_pacific.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Weekly reset:         {reset_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    for log_url, label in LOG_URLS:
        page.goto(f"https://lostark.bible{log_url}", timeout=60000)
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)
        
        body_text = page.locator("body").inner_text()
        ts_match = re.search(r'(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)', body_text)
        
        if ts_match:
            ts_str = ts_match.group(1)
            naive_dt = datetime.strptime(ts_str, "%m/%d/%Y %I:%M %p")
            dt = naive_dt.replace(tzinfo=PACIFIC_TZ)
            after_reset = dt >= reset_time
            print(f"{label}")
            print(f"  Timestamp: {ts_str} → {dt.strftime('%Y-%m-%d %I:%M %p %Z')}")
            print(f"  After reset? {after_reset} ({'✅ THIS WEEK' if after_reset else '❌ LAST WEEK'})")
        else:
            print(f"{label}: NO TIMESTAMP FOUND")
        print()
    
    browser.close()

