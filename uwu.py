from playwright.sync_api import sync_playwright
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE_URL = "https://lostark.bible"
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def get_last_weekly_reset():
    """Return the datetime of the most recent Wednesday 3AM Pacific (Lost Ark weekly reset)."""
    now = datetime.now(PACIFIC_TZ)
    # Wednesday is weekday() == 2
    days_since_wed = (now.weekday() - 2) % 7
    last_wed = now - timedelta(days=days_since_wed)
    reset_time = last_wed.replace(hour=3, minute=0, second=0, microsecond=0)
    # If that Wednesday 3AM hasn't happened yet, go back one more week
    if reset_time > now:
        reset_time -= timedelta(weeks=1)
    return reset_time


def calculate_absolute_datetime(relative_string):
    """Return a timezone-aware datetime (Pacific) parsed from a relative time string."""
    now = datetime.now(PACIFIC_TZ)
    
    # Catches sec, min, mins, hr, hrs, hour, hours, day, days, months, etc.
    match = re.search(r'\b(\d+)\s+(sec|second|mins?|minutes?|hrs?|hours?|days?|weeks?|months?)\s+ago\b', relative_string, re.IGNORECASE)
    
    if match:
        value = int(match.group(1))
        unit = match.group(2).lower()
        
        if unit.startswith('sec'):
            delta = timedelta(seconds=value)
        elif unit.startswith('min'):
            delta = timedelta(minutes=value)
        elif unit.startswith('hr') or unit.startswith('hour'):
            delta = timedelta(hours=value)
        elif unit.startswith('day'):
            delta = timedelta(days=value)
        elif unit.startswith('week'):
            delta = timedelta(weeks=value)
        elif unit.startswith('mon'):
            delta = timedelta(days=value * 30)  # approximate: 1 month ≈ 30 days
        else:
            return None
            
        return now - delta
    
    return None


def get_roster_characters(page, main_character):
    """Navigate to the roster page and return a list of (name, item_level) tuples."""
    roster_url = f"{BASE_URL}/character/NA/{main_character}/roster"
    print(f"Fetching roster for {main_character}...")
    page.goto(roster_url, timeout=60000)
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Roster character links are like /character/NA/{Name}
    # The page also has navigation tab links (e.g. "Character", "Roster") with the same href pattern.
    # We distinguish roster cards from nav tabs by checking that the link text starts with the character name.
    links = page.locator('a[href*="/character/"]')
    characters = []
    seen = set()
    for i in range(links.count()):
        href = links.nth(i).get_attribute("href") or ""
        # Only match exact character page links: /character/NA/{Name}
        match = re.match(r'^/character/NA/([^/]+)$', href)
        if match:
            name = match.group(1)
            if name not in seen:
                raw_text = links.nth(i).inner_text().strip()
                # Roster cards start with the character name; nav tabs say "Character", "Roster", etc.
                if not raw_text.startswith(name):
                    continue
                seen.add(name)
                # Extract item level from the link's inner text
                # The item level is the first standalone number (e.g. 1747.5, 1740)
                # appearing after the character name. Combat power has "≈" prefix.
                item_level = "N/A"
                for line in raw_text.split('\n'):
                    line = line.strip()
                    if re.match(r'^\d{3,4}(\.\d+)?$', line):
                        item_level = line
                        break
                characters.append((name, item_level))

    print(f"Found {len(characters)} characters in roster: {', '.join(c[0] for c in characters)}\n")
    return characters


def get_exact_timestamp(page, log_url):
    """Visit an individual log page and extract the precise timestamp.
    
    The log page may contain timestamps in one of several formats:
      - Absolute: '04/12/2026 11:58 PM'
      - Relative: 'Today @ 2:57 AM'
      - Relative: 'Yesterday @ 3:14 PM'
    Returns a timezone-aware datetime in Pacific time, or None.
    """
    full_url = f"{BASE_URL}{log_url}" if log_url.startswith("/") else log_url
    page.goto(full_url, timeout=60000)
    page.wait_for_load_state("networkidle")
    time.sleep(0.5)

    body_text = page.locator("body").inner_text()
    now = datetime.now(PACIFIC_TZ)

    # Try absolute format: MM/DD/YYYY HH:MM AM/PM
    ts_match = re.search(r'(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)', body_text)
    if ts_match:
        ts_str = ts_match.group(1)
        try:
            naive_dt = datetime.strptime(ts_str, "%m/%d/%Y %I:%M %p")
            return naive_dt.replace(tzinfo=PACIFIC_TZ)
        except ValueError:
            pass

    # Try "Today @ HH:MM AM/PM"
    today_match = re.search(r'Today\s*@\s*(\d{1,2}:\d{2}\s+[AP]M)', body_text, re.IGNORECASE)
    if today_match:
        ts_str = today_match.group(1)
        try:
            time_dt = datetime.strptime(ts_str, "%I:%M %p")
            return now.replace(hour=time_dt.hour, minute=time_dt.minute, second=0, microsecond=0)
        except ValueError:
            pass

    # Try "Yesterday @ HH:MM AM/PM"
    yest_match = re.search(r'Yesterday\s*@\s*(\d{1,2}:\d{2}\s+[AP]M)', body_text, re.IGNORECASE)
    if yest_match:
        ts_str = yest_match.group(1)
        try:
            time_dt = datetime.strptime(ts_str, "%I:%M %p")
            yesterday = now - timedelta(days=1)
            return yesterday.replace(hour=time_dt.hour, minute=time_dt.minute, second=0, microsecond=0)
        except ValueError:
            pass

    return None


def scrape_character_logs(page, character_name):
    """Scrape the logs page for a single character. Returns raw_entries list."""
    url = f"{BASE_URL}/character/NA/{character_name}/logs"
    print(f"  Loading logs for {character_name}...")
    page.goto(url, timeout=60000)
    page.wait_for_load_state("networkidle")

    # Scroll to the bottom to load everything
    last_height = page.evaluate("document.body.scrollHeight")
    while True:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    # Get all the log rows
    log_rows = page.locator("div.hover\\:bg-surface-900")
    count = log_rows.count()

    raw_entries = []

    for i in range(count):
        row = log_rows.nth(i)
        
        link_el = row.locator("a")
        boss_name = link_el.inner_text().strip()
        log_href = link_el.get_attribute("href") or ""
        
        # Extract the gate tag (e.g. "Kazeros G2") from <p> elements in the row
        gate_tag = ""
        gate_number = 0
        raid_base = ""
        p_elements = row.locator("p")
        for j in range(p_elements.count()):
            p_text = p_elements.nth(j).inner_text().strip()
            gate_match = re.match(r'^(.+?)\s+G(\d+)$', p_text)
            if gate_match:
                gate_tag = p_text
                raid_base = gate_match.group(1)
                gate_number = int(gate_match.group(2))
                break
        
        if boss_name:
            raid_display = f"{boss_name} ({gate_tag})" if gate_tag else boss_name
            raw_entries.append({
                "character": character_name,
                "raid_display": raid_display,
                "raid_base": raid_base,
                "gate_number": gate_number,
                "log_href": log_href,
            })

    # Keep only the most recent completion of each raid
    # The page lists logs in chronological order (most recent first),
    # so the first entry for each raid_base is the most recent.
    filtered = []
    seen_raids = set()
    for entry in raw_entries:
        rb = entry["raid_base"]
        if not rb or rb not in seen_raids:
            filtered.append(entry)
            if rb:
                seen_raids.add(rb)

    # Visit each filtered log page to get the exact timestamp
    print(f"    → {len(filtered)} most-recent raid entries, fetching exact timestamps...")
    for entry in filtered:
        if entry["log_href"]:
            exact_dt = get_exact_timestamp(page, entry["log_href"])
            entry["completion_dt"] = exact_dt
        else:
            entry["completion_dt"] = None

    return filtered


def print_raid_logs(main_character):
    print(f"Launching browser for {main_character}...\n")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1. Get all character names from the roster
        character_names = get_roster_characters(page, main_character)

        # 2. Determine the weekly reset boundary
        reset_time = get_last_weekly_reset()
        now_pacific = datetime.now(PACIFIC_TZ)
        print(f"Current time (Pacific):   {now_pacific.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Weekly reset (Wednesday): {reset_time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")

        # 3. For each character, scrape their logs
        all_entries = []

        for char_name, _item_level in character_names:
            entries = scrape_character_logs(page, char_name)
            all_entries.extend(entries)

        browser.close()

        # 4. Collect all unique raid names (raid_base) across all characters
        raid_names = []
        seen_raids = set()
        for entry in all_entries:
            rb = entry["raid_base"]
            if rb and rb not in seen_raids:
                seen_raids.add(rb)
                raid_names.append(rb)
        # Also include entries without a raid_base (no gate tag) — use raid_display
        # These are appended as-is after gated raids
        non_gated_raids = []
        for entry in all_entries:
            if not entry["raid_base"]:
                rd = entry["raid_display"]
                if rd not in seen_raids:
                    seen_raids.add(rd)
                    non_gated_raids.append(rd)

        all_raid_cols = raid_names + non_gated_raids

        # 5. Build a lookup: (character, raid_col) → completion status symbol
        # For each character, find the most recent entry per raid_col
        char_raid_status = {}  # (char_name, raid_col) → "✅", "❌", or "❓"
        for entry in all_entries:
            rb = entry["raid_base"] or entry["raid_display"]
            char = entry["character"]
            dt = entry["completion_dt"]
            if dt:
                status = "✅" if dt >= reset_time else "❌"
            else:
                status = "❓"
            # Only store if not already set (first = most recent per character)
            if (char, rb) not in char_raid_status:
                char_raid_status[(char, rb)] = status

        # 6. Build the crosstab table
        num_cols = 2 + len(all_raid_cols)  # Character Name + Item Level + raid columns
        header = ["Character", "iLvl"] + all_raid_cols
        rows = []
        for char_name, item_level in character_names:
            row = [char_name, item_level]
            for raid_col in all_raid_cols:
                status = char_raid_status.get((char_name, raid_col), "—")
                row.append(status)
            rows.append(row)

        # 7. Calculate dynamic column widths
        all_table = [header] + rows
        col_widths = [
            max(len(str(row[i])) for row in all_table) + 2
            for i in range(num_cols)
        ]

        # 8. Print the table to the terminal
        separator = "-" * sum(col_widths)

        print(f"\n{'='*60}")
        print(f"  RAID LOGS — WEEKLY COMPLETION STATUS")
        print(f"  Reset window: {reset_time.strftime('%Y-%m-%d %H:%M')} → {now_pacific.strftime('%Y-%m-%d %H:%M')} Pacific")
        print(f"{'='*60}\n")

        print(separator)
        # Print Header
        header_line = " ".join(f"{header[i]:<{col_widths[i]}}" for i in range(num_cols))
        print(header_line)
        print(separator)
        
        # Print Data Rows
        for row in rows:
            row_line = " ".join(f"{row[i]:<{col_widths[i]}}" for i in range(num_cols))
            print(row_line)
            
        print(separator)
        
        # Summary: count completions this week vs total
        total_cells = len(character_names) * len(all_raid_cols)
        completed_count = sum(
            1 for entry in all_entries
            if entry["completion_dt"] and entry["completion_dt"] >= reset_time
        )
        # Deduplicate by (character, raid_base) for accurate count
        unique_completed = set()
        for entry in all_entries:
            rb = entry["raid_base"] or entry["raid_display"]
            if entry["completion_dt"] and entry["completion_dt"] >= reset_time:
                unique_completed.add((entry["character"], rb))
        completed_count = len(unique_completed)
        print(f"\nCompleted this week: {completed_count}/{total_cells} (across {len(character_names)} characters, {len(all_raid_cols)} raids)")

if __name__ == "__main__":
    character = input("Enter character name: ").strip()
    if character:
        character = character[0].upper() + character[1:]
        print_raid_logs(character)
    else:
        print("No character name provided. Exiting.")
