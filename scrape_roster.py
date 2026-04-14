from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://lostark.bible/character/NA/Sunileah/roster', timeout=60000)
    page.wait_for_load_state('networkidle')
    time.sleep(2)
    
    # Get the page title
    print('=== PAGE TITLE ===')
    print(page.title())
    
    # Get the main content area - look for roster-related elements
    print('\n=== H1 ===')
    h1 = page.locator('h1')
    for i in range(h1.count()):
        print(h1.nth(i).inner_text())
    
    print('\n=== H2 ===')
    h2 = page.locator('h2')
    for i in range(h2.count()):
        print(h2.nth(i).inner_text())
    
    print('\n=== LINKS with /character/ ===')
    links = page.locator('a[href*="/character/"]')
    count = links.count()
    print(f'Found {count} character links')
    for i in range(min(count, 30)):
        href = links.nth(i).get_attribute('href')
        text = links.nth(i).inner_text().strip()
        print(f'  [{i}] href={href}  text={text}')
    
    browser.close()
