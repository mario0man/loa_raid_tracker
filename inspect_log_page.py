from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    
    # Visit a log page - using one of the log IDs from the HTML
    page.goto('https://lostark.bible/logs/Q0jNzPm', timeout=60000)
    page.wait_for_load_state('networkidle')
    time.sleep(2)
    
    print('=== PAGE TITLE ===')
    print(page.title())
    
    print('\n=== H1 ===')
    h1 = page.locator('h1')
    for i in range(h1.count()):
        print(h1.nth(i).inner_text())
    
    print('\n=== H2 ===')
    h2 = page.locator('h2')
    for i in range(h2.count()):
        print(h2.nth(i).inner_text())
    
    print('\n=== H3 ===')
    h3 = page.locator('h3')
    for i in range(h3.count()):
        print(h3.nth(i).inner_text())
    
    print('\n=== ALL TEXT (body) ===')
    print(page.locator('body').inner_text()[:3000])
    
    browser.close()
