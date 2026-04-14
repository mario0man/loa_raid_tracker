from playwright.sync_api import sync_playwright
import re
import time

BASE_URL = "https://lostark.bible"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    page.goto(f"{BASE_URL}/character/NA/Sunileah/logs", timeout=60000)
    page.wait_for_load_state("networkidle")
    
    # Scroll to load everything
    last_height = page.evaluate("document.body.scrollHeight")
    scroll_count = 0
    while True:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = page.evaluate("document.body.scrollHeight")
        scroll_count += 1
        print(f"Scroll #{scroll_count}: height {last_height} → {new_height}")
        if new_height == last_height:
            break
        last_height = new_height
    
    print(f"\nTotal scrolls: {scroll_count}")
    
    # Get all log rows
    log_rows = page.locator("div.hover\\:bg-surface-900")
    count = log_rows.count()
    print(f"Total log rows found: {count}\n")
    
    # Show ALL rows, highlighting Mordum ones
    print("=" * 80)
    print("ALL LOG ROWS:")
    print("=" * 80)
    for i in range(count):
        row = log_rows.nth(i)
        boss_name = row.locator("a").inner_text().strip()
        log_href = row.locator("a").get_attribute("href") or ""
        
        # Get all <p> text
        p_texts = []
        p_elements = row.locator("p")
        for j in range(p_elements.count()):
            p_texts.append(p_elements.nth(j).inner_text().strip())
        
        # Get last div text (timestamp area)
        relative_time = row.locator(":scope > div").last.inner_text().strip()
        
        gate_tag = ""
        raid_base = ""
        for pt in p_texts:
            gate_match = re.match(r'^(.+?)\s+G(\d+)$', pt)
            if gate_match:
                gate_tag = pt
                raid_base = gate_match.group(1)
        
        marker = " ◄◄◄ MORDUM" if raid_base == "Mordum" else ""
        print(f"  [{i:2d}] boss={boss_name:40s} gate={gate_tag:12s} raid_base={raid_base:12s} href={log_href:15s} time={relative_time}{marker}")
    
    browser.close()
