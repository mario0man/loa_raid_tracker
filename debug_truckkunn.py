"""Debug script to investigate why Truckkunn's raid logs show '?' (no timestamp)."""
from playwright.sync_api import sync_playwright
import re
import time

BASE_URL = "https://lostark.bible"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # 1. Load Truckkunn's logs page
    url = f"{BASE_URL}/character/NA/Truckkunn/logs"
    print(f"Loading logs page: {url}")
    page.goto(url, timeout=60000)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('div.hover\\:bg-surface-900', timeout=15000, state="attached")

    # Scroll to load everything
    last_height = page.evaluate("document.body.scrollHeight")
    while True:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.5)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    # 2. Extract all log row data
    raw_entries = page.evaluate("""() => {
        const rows = document.querySelectorAll('div.hover\\\\:bg-surface-900');
        return Array.from(rows).map(row => {
            const link = row.querySelector('a');
            const bossName = link ? link.innerText.trim() : '';
            const logHref = link ? (link.getAttribute('href') || '') : '';
            const pElements = row.querySelectorAll('p');
            let gateTag = '';
            let raidBase = '';
            let gateNumber = 0;
            for (const p of pElements) {
                const pText = p.innerText.trim();
                const gateMatch = pText.match(/^(.+?)\\s+G(\\d+)$/);
                if (gateMatch) {
                    gateTag = pText;
                    raidBase = gateMatch[1];
                    gateNumber = parseInt(gateMatch[2]);
                    break;
                }
            }
            return { bossName, logHref, gateTag, raidBase, gateNumber };
        });
    }""")

    # 3. Filter to most recent per raid_base
    seen_raids = set()
    filtered = []
    for entry in raw_entries:
        rb = entry['raidBase']
        if rb and rb in seen_raids:
            continue
        filtered.append(entry)
        if rb:
            seen_raids.add(rb)

    print(f"\nFound {len(raw_entries)} total entries, {len(filtered)} unique raids:")
    for e in filtered:
        print(f"  {e['bossName']} | gate={e['gateTag']} | raid_base={e['raidBase']} | href={e['logHref']}")

    # 4. Visit each filtered log page and dump the body text to find timestamps
    print("\n" + "="*80)
    print("VISITING INDIVIDUAL LOG PAGES:")
    print("="*80)
    
    for entry in filtered:
        log_href = entry['logHref']
        if not log_href:
            print(f"\n--- {entry['bossName']} ({entry['gateTag']}) --- NO HREF, SKIPPING")
            continue

        full_url = f"{BASE_URL}{log_href}" if log_href.startswith("/") else log_href
        print(f"\n--- {entry['bossName']} ({entry['gateTag']}) --- {full_url}")
        page.goto(full_url, timeout=60000)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1)

        body_text = page.evaluate("() => document.body.innerText")
        
        # Search for timestamp patterns
        abs_match = re.search(r'(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)', body_text)
        today_match = re.search(r'(Today\s*@\s*\d{1,2}:\d{2}\s+[AP]M)', body_text, re.IGNORECASE)
        yest_match = re.search(r'(Yesterday\s*@\s*\d{1,2}:\d{2}\s+[AP]M)', body_text, re.IGNORECASE)
        
        # Also look for any date-like patterns
        date_patterns = re.findall(r'(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|Today|Yesterday)', body_text, re.IGNORECASE)
        
        if abs_match:
            print(f"  ✅ ABSOLUTE MATCH: {abs_match.group(1)}")
        elif today_match:
            print(f"  ✅ TODAY MATCH: {today_match.group(1)}")
        elif yest_match:
            print(f"  ✅ YESTERDAY MATCH: {yest_match.group(1)}")
        else:
            print(f"  ❌ NO TIMESTAMP MATCH")
            print(f"  Date-like patterns found: {date_patterns[:10]}")
            # Print a snippet around any "@" signs
            at_snippets = [(m.start(), body_text[max(0,m.start()-20):m.end()+20]) for m in re.finditer(r'@', body_text)]
            if at_snippets:
                print(f"  '@' context snippets:")
                for pos, snippet in at_snippets[:5]:
                    print(f"    ...{snippet!r}...")
            # Print first 500 chars for debugging
            print(f"  First 500 chars of body:")
            print(f"  {body_text[:500]}")

    browser.close()
