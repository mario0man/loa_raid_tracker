"""Debug: Test if get_exact_timestamp captures timestamps without sleep."""
from playwright.sync_api import sync_playwright
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
BASE_URL = "https://lostark.bible"

# Simulate what uwu2.py does (no sleep, just selector wait)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    test_urls = [
        "/logs/BKCrfQp",  # Kazeros G2
        "/logs/vyVjECz",  # Armoche G2
        "/logs/MYIZA1A",  # Mordum G3
    ]

    for log_url in test_urls:
        full_url = f"{BASE_URL}{log_url}"
        page.goto(full_url, timeout=60000)
        page.wait_for_load_state("domcontentloaded")
        # This is what uwu2.py does - no sleep, just selector wait
        page.wait_for_selector('body', timeout=10000, state="attached")

        # Immediately grab body text (same as uwu2.py)
        body_text = page.evaluate("() => document.body.innerText")

        abs_match = re.search(r'(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)', body_text)
        today_match = re.search(r'Today\s*@\s*\d{1,2}:\d{2}\s+[AP]M', body_text, re.IGNORECASE)
        yest_match = re.search(r'Yesterday\s*@\s*\d{1,2}:\d{2}\s+[AP]M', body_text, re.IGNORECASE)

        print(f"\n{log_url}:")
        print(f"  Body length: {len(body_text)} chars")
        if abs_match:
            print(f"  ✅ ABSOLUTE: {abs_match.group(1)}")
        elif today_match:
            print(f"  ✅ TODAY: {today_match.group(1)}")
        elif yest_match:
            print(f"  ✅ YESTERDAY: {yest_match.group(1)}")
        else:
            print(f"  ❌ NO MATCH - first 300 chars:")
            print(f"  {body_text[:300]}")

    browser.close()
