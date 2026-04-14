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
    page.wait_for_load_state("domcontentloaded")
    # MOD 3: selector-based wait instead of fixed sleep
    page.wait_for_selector('a[href*="/character/"]', timeout=15000, state="attached")

    # MOD 2: Use JS to extract all roster link data in one evaluate call
    raw_links = page.evaluate("""() => {
        const links = document.querySelectorAll('a[href*="/character/"]');
        return Array.from(links).map(a => ({
            href: a.getAttribute('href') || '',
            text: a.innerText.trim()
        }));
    }""")

    characters = []
    seen = set()
    for link_data in raw_links:
        href = link_data['href']
        raw_text = link_data['text']
        # Only match exact character page links: /character/NA/{Name}
        match = re.match(r'^/character/NA/([^/]+)$', href)
        if match:
            name = match.group(1)
            if name not in seen:
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
    # Retry page.goto up to 3 times in case of server rate-limiting / timeout
    for attempt in range(3):
        try:
            page.goto(full_url, timeout=60000)
            page.wait_for_load_state("domcontentloaded")
            break
        except Exception as e:
            if attempt < 2:
                print(f"    ⚠ Timeout loading {full_url}, retrying ({attempt+2}/3)...")
                time.sleep(2)
            else:
                print(f"    ⚠ Failed to load {full_url} after 3 attempts: {e}")
                return None

    # MOD 3: Wait until a timestamp pattern actually appears in the body text.
    # This is more robust than waiting for a selector, since the timestamp is
    # rendered dynamically and may not be present immediately after DOMContentLoaded.
    try:
        page.wait_for_function(r"""() => {
            const text = document.body.innerText;
            return /\d{2}\/\d{2}\/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M/.test(text)
                || /Today\s*@\s*\d{1,2}:\d{2}\s+[AP]M/i.test(text)
                || /Yesterday\s*@\s*\d{1,2}:\d{2}\s+[AP]M/i.test(text);
        }""", timeout=10000)
    except Exception:
        pass  # Proceed anyway — body text will be checked below

    # MOD 2: Use JS to extract body text in one call
    body_text = page.evaluate("() => document.body.innerText")
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
    page.wait_for_load_state("domcontentloaded")
    # MOD 3: selector-based wait instead of fixed sleep
    # Some characters may have no logs at all, so use a short timeout and handle gracefully
    has_logs = True
    try:
        page.wait_for_selector('div.hover\\:bg-surface-900', timeout=5000, state="attached")
    except Exception:
        has_logs = False

    if not has_logs:
        print(f"    → No log entries found for {character_name}.")
        return []

    # Scroll to the bottom to load everything
    # MOD 4: reduced scroll delay from 1.5s to 0.5s
    last_height = page.evaluate("document.body.scrollHeight")
    while True:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.5)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    # MOD 2: Use JS to extract all log row data in a single evaluate call
    # Also extracts the relative timestamp text from each row
    raw_entries_data = page.evaluate(r"""() => {
        const rows = document.querySelectorAll('div.hover\\:bg-surface-900');
        return Array.from(rows).map(row => {
            const link = row.querySelector('a');
            const bossName = link ? link.innerText.trim() : '';
            const logHref = link ? (link.getAttribute('href') || '') : '';
            
            // Extract gate tag from <p> elements
            const pElements = row.querySelectorAll('p');
            let gateTag = '';
            let raidBase = '';
            let gateNumber = 0;
            for (const p of pElements) {
                const pText = p.innerText.trim();
                const gateMatch = pText.match(/^(.+?)\s+G(\d+)$/);
                if (gateMatch) {
                    gateTag = pText;
                    raidBase = gateMatch[1];
                    gateNumber = parseInt(gateMatch[2]);
                    break;
                }
            }
            
            // Extract relative timestamp from row text (e.g. "6 hours ago", "2 days ago")
            const rowText = row.innerText;
            const relTimeMatch = rowText.match(/(\d+\s+(?:sec|second|mins?|minutes?|hrs?|hours?|days?|weeks?|months?)\s+ago)/i);
            const relativeTime = relTimeMatch ? relTimeMatch[0] : '';
            
            return { bossName, logHref, gateTag, raidBase, gateNumber, relativeTime };
        });
    }""")

    raw_entries = []
    for entry_data in raw_entries_data:
        boss_name = entry_data['bossName']
        gate_tag = entry_data['gateTag']
        raid_base = entry_data['raidBase']
        gate_number = entry_data['gateNumber']
        log_href = entry_data['logHref']
        relative_time = entry_data['relativeTime']

        if boss_name:
            raid_display = f"{boss_name} ({gate_tag})" if gate_tag else boss_name
            raw_entries.append({
                "character": character_name,
                "raid_display": raid_display,
                "raid_base": raid_base,
                "gate_number": gate_number,
                "log_href": log_href,
                "relative_time": relative_time,
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

    # Determine the reset boundary window for the optimization:
    # Only visit individual log pages if the relative timestamp places the log
    # on Tuesday or Wednesday (the weekly reset boundary at Wed 3AM Pacific).
    # For all other days, the relative timestamp is precise enough.
    reset_time = get_last_weekly_reset()
    now_pacific = datetime.now(PACIFIC_TZ)

    # The window where exact timestamps are needed: Tuesday 00:00 to Wednesday 23:59:59
    # (the reset is at Wed 3AM, so any log on Tue or Wed could be near the boundary)
    tues_start = reset_time - timedelta(days=1)  # Tuesday 3AM
    tues_start = tues_start.replace(hour=0, minute=0, second=0, microsecond=0)  # Tue 00:00
    wed_end = reset_time.replace(hour=23, minute=59, second=59, microsecond=999999)  # Wed 23:59:59

    exact_count = 0
    relative_count = 0

    for entry in filtered:
        # Parse the relative timestamp to get an approximate datetime
        approx_dt = calculate_absolute_datetime(entry.get("relative_time", ""))

        # Check if the approximate datetime falls near the reset boundary (Tue/Wed)
        needs_exact = False
        if approx_dt is None:
            # Can't parse relative time — must visit the log page
            needs_exact = True
        elif tues_start <= approx_dt <= wed_end:
            # Log is on Tuesday or Wednesday — could be near the Wed 3AM boundary
            needs_exact = True
        elif approx_dt >= reset_time:
            # Clearly after the reset — no need for exact timestamp
            entry["completion_dt"] = approx_dt
            relative_count += 1
            continue
        else:
            # Clearly before the reset — no need for exact timestamp
            entry["completion_dt"] = approx_dt
            relative_count += 1
            continue

        if needs_exact and entry["log_href"]:
            exact_dt = get_exact_timestamp(page, entry["log_href"])
            entry["completion_dt"] = exact_dt
            exact_count += 1
        else:
            entry["completion_dt"] = approx_dt

    print(f"    → {len(filtered)} raids: {exact_count} exact lookups, {relative_count} from relative time")

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
                status = char_raid_status.get((char_name, raid_col), "➖")
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
        character = character[0].upper() + character[1:].lower()
        print_raid_logs(character)
    else:
        print("No character name provided. Exiting.")
