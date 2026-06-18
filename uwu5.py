import requests
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

BASE_URL = "https://lostark.bible"
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# Kazeros has different boss names per difficulty for gate 2, and the JS mapping is wrong.
KAZEROS_OVERRIDE = {
    ("Abyss Lord Kazeros", "Hard"): ("Kazeros", 1),
    ("Abyss Lord Kazeros", "Normal"): ("Kazeros", 1),
    ("Death Incarnate Kazeros", "Hard"): ("Kazeros", 2),
    ("Archdemon Kazeros", "Normal"): ("Kazeros", 2),
}

# Horizon Cathedral — new raid released 2026-06-17 (2 gates).
# Explicitly registered so the completion matrix always includes it, independent
# of the live lostark.bible boss->raid mapping (which can lag on release day).
HORIZON_CATHEDRAL_BOSSES = {
    "Archbishop Arcenos": ("Horizon Cathedral", 1),
    "Arcenos, Vanguard of Fanaticism": ("Horizon Cathedral", 2),
}

# Explicitly-registered raids, newest first. These are layered ON TOP of the
# dynamic lostark.bible mapping and never alter any existing raid's mapping.
EXPLICIT_RAIDS = [
    ("Horizon Cathedral", HORIZON_CATHEDRAL_BOSSES),
]

# Shared session for connection pooling
_session = requests.Session()
_session.headers.update(HEADERS)


def fetch_page(path):
    """Fetch HTML from lostark.bible with retry, using the shared session."""
    url = f"{BASE_URL}{path}"
    for attempt in range(3):
        try:
            resp = _session.get(url, timeout=60)
            resp.encoding = "utf-8"
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            if attempt < 2:
                print(f"    [!] Request failed, retrying ({attempt + 2}/3): {e}")
                time.sleep(3)
            else:
                print(f"    [!] Failed after 3 attempts: {e}")
                return ""


def _fetch_boss_to_raid():
    """Fetch the boss->raid mapping from lostark.bible's SvelteKit JS."""
    # 1. Get any page to find the app entry JS filename
    resp = _session.get(f"{BASE_URL}/character/NA/test/logs", timeout=60)
    resp.encoding = "utf-8"
    app_match = re.search(r'entry/app\.([^"]+\.js)', resp.text)
    if not app_match:
        return {}, []
    app_url = f"{BASE_URL}/_app/immutable/entry/app.{app_match.group(1)}"

    # 2. Fetch the app JS and find node 18's filename
    app_js = _session.get(app_url, timeout=60).text
    node18_match = re.search(r'nodes/18\.([^"]+\.js)', app_js)
    if not node18_match:
        return {}, []
    node18_url = f"{BASE_URL}/_app/immutable/nodes/18.{node18_match.group(1)}"

    # 3. Fetch node 18 and parse the raid mapping
    node18_js = _session.get(node18_url, timeout=60).text

    start = node18_js.find("={")
    if start == -1:
        return {}, []
    start += 1
    depth = 1
    end = start
    for i in range(start + 1, len(node18_js)):
        if node18_js[i] == "{":
            depth += 1
        elif node18_js[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    mapping_text = node18_js[start + 1 : end]

    boss_to_raid = {}
    raid_order = []
    for raid_match in re.finditer(r'(.+?):\[(.*?)\]', mapping_text):
        raid_name = raid_match.group(1).strip().strip('`",')
        raid_order.append(raid_name)
        bosses_text = raid_match.group(2)
        for gate_num, boss_match in enumerate(re.finditer(r'`([^`]+)`', bosses_text), 1):
            boss_to_raid[boss_match.group(1)] = (raid_name, gate_num)
    return boss_to_raid, raid_order


def _register_explicit_raids(boss_to_raid, raid_order):
    """Layer explicitly-registered raids on top of the live mapping.

    Only injects the new raid definitions declared in EXPLICIT_RAIDS — it never
    touches any other raid's mapping. The explicit raids are forced to the front
    of raid_order (newest first) so they always render as columns in the matrix,
    even when no character has logged them within the 3-week recency window.
    """
    boss_to_raid = dict(boss_to_raid)
    raid_order = list(raid_order)
    for raid_name, bosses in EXPLICIT_RAIDS:
        boss_to_raid.update(bosses)
        if raid_name in raid_order:
            raid_order.remove(raid_name)
        raid_order.insert(0, raid_name)
    return boss_to_raid, raid_order


def get_last_weekly_reset():
    """Return the datetime of the most recent Wednesday 3AM Pacific."""
    now = datetime.now(PACIFIC_TZ)
    days_since_wed = (now.weekday() - 2) % 7
    last_wed = now - timedelta(days=days_since_wed)
    reset_time = last_wed.replace(hour=3, minute=0, second=0, microsecond=0)
    if reset_time > now:
        reset_time -= timedelta(weeks=1)
    return reset_time


def _extract_bracket_text(html, key):
    """Find `key:[` in html and return the text between the [ and its matching ]."""
    start = html.find(f"{key}:[")
    if start == -1:
        return ""
    open_pos = start + len(key) + 1
    depth = 1
    for i in range(open_pos + 1, len(html)):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                return html[open_pos + 1 : i]
    return ""


def _format_ilvl(val):
    """Format item level: integer if whole, one decimal otherwise."""
    f = float(val)
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def extract_roster(html):
    """Extract (name, ilvl_display) pairs from the roster SvelteKit data."""
    roster_text = _extract_bracket_text(html, "roster")
    if not roster_text:
        return []
    characters = []
    for m in re.finditer(r'name:"([^"]+)"[^}]*?ilvl:([\d.]+)', roster_text):
        characters.append((m.group(1), _format_ilvl(m.group(2))))
    return characters


def extract_logs(html, boss_to_raid):
    """Extract log entries from the logs SvelteKit data."""
    logs_text = _extract_bracket_text(html, "logs")
    if not logs_text:
        return []
    bosses = re.findall(r'boss:"([^"]+)"', logs_text)
    timestamps = re.findall(r"timestamp:(\d+)", logs_text)
    difficulties = re.findall(r'difficulty:"([^"]+)"', logs_text)
    entries = []
    for boss, ts, difficulty in zip(bosses, timestamps, difficulties):
        ts_ms = int(ts)
        override = KAZEROS_OVERRIDE.get((boss, difficulty))
        raid_info = override or boss_to_raid.get(boss)
        entries.append({
            "boss": boss,
            "raid_name": raid_info[0] if raid_info else boss,
            "gate_number": raid_info[1] if raid_info else 0,
            "completion_dt": datetime.fromtimestamp(ts_ms / 1000, tz=PACIFIC_TZ),
        })
    return entries


def _fetch_character_logs(char_name, boss_to_raid):
    """Fetch and parse logs for a single character. Returns (char_name, entries, seen_count, total_count)."""
    logs_html = fetch_page(f"/character/NA/{quote(char_name)}/logs")
    if not logs_html:
        return char_name, [], 0, 0
    entries = extract_logs(logs_html, boss_to_raid)
    if not entries:
        return char_name, [], 0, 0

    seen_bosses = set()
    unique_entries = []
    for entry in entries:
        if entry["boss"] not in seen_bosses:
            seen_bosses.add(entry["boss"])
            entry["character"] = char_name
            unique_entries.append(entry)

    return char_name, unique_entries, len(seen_bosses), len(entries)


def print_raid_logs(main_character):
    print(f"Fetching data for {main_character}...\n")

    # 1. Fetch boss mapping and roster in parallel
    print(f"Fetching roster and raid mapping for {main_character}...")
    with ThreadPoolExecutor(max_workers=2) as executor:
        roster_future = executor.submit(fetch_page, f"/character/NA/{main_character}/roster")
        mapping_future = executor.submit(_fetch_boss_to_raid)

        boss_to_raid, raid_order = mapping_future.result()
        roster_html = roster_future.result()

    # Layer explicitly-registered raids (e.g. Horizon Cathedral) on top of the
    # live mapping without altering any existing raid's mapping.
    boss_to_raid, raid_order = _register_explicit_raids(boss_to_raid, raid_order)

    if not roster_html:
        print("Failed to load roster page.")
        return
    character_names = extract_roster(roster_html)
    if not character_names:
        print(f"No characters found in roster for {main_character}.")
        return
    print(f"Found {len(character_names)} characters: {', '.join(c[0] for c in character_names)}\n")

    # 2. Weekly reset boundary
    reset_time = get_last_weekly_reset()
    now_pacific = datetime.now(PACIFIC_TZ)
    print(f"Current time (Pacific):   {now_pacific.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Weekly reset (Wednesday): {reset_time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")

    # 3. Fetch logs for all characters in parallel
    print(f"Fetching logs for {len(character_names)} characters in parallel...")
    all_entries = []
    with ThreadPoolExecutor(max_workers=min(len(character_names), 8)) as executor:
        futures = {
            executor.submit(_fetch_character_logs, char_name, boss_to_raid): char_name
            for char_name, _ in character_names
        }
        for future in as_completed(futures):
            char_name, entries, unique_count, total_count = future.result()
            if entries:
                all_entries.extend(entries)
                print(f"  {char_name}: {total_count} logs, {unique_count} unique bosses")
            else:
                print(f"  {char_name}: No log entries found")

    if not all_entries:
        print("\nNo log entries found for any character.")
        return

    # 4. Collect unique raid names, ordered by raid_order (newest first)
    three_weeks_ago = now_pacific - timedelta(weeks=3)
    newest_per_raid = {}
    for entry in all_entries:
        raid = entry["raid_name"]
        if raid not in newest_per_raid or entry["completion_dt"] > newest_per_raid[raid]:
            newest_per_raid[raid] = entry["completion_dt"]
    raids_in_data = {r for r, dt in newest_per_raid.items() if dt >= three_weeks_ago}
    raid_names = [r for r in raid_order if r in raids_in_data]

    # 5. Sort characters by ilvl descending, then alphabetical
    character_names.sort(key=lambda c: (-float(c[1]), c[0]))

    # 6. Build completion status lookup
    # A raid is complete only if ALL gates the character has done were completed this week
    raid_has_logs = set()
    char_raid_gates = {}
    gates_this_week = {}
    for entry in all_entries:
        key = (entry["character"], entry["raid_name"])
        raid_has_logs.add(key)
        if key not in char_raid_gates:
            char_raid_gates[key] = set()
        char_raid_gates[key].add(entry["gate_number"])
        if entry["completion_dt"] >= reset_time:
            if key not in gates_this_week:
                gates_this_week[key] = set()
            gates_this_week[key].add(entry["gate_number"])

    char_raid_status = {}
    for key in raid_has_logs:
        done = gates_this_week.get(key, set())
        all_gates = char_raid_gates[key]
        if len(done) == len(all_gates) or (done and max(done) == max(all_gates)):
            char_raid_status[key] = "✅"
        else:
            char_raid_status[key] = "❌"

    # 7. Build crosstab table
    header = ["Character", "iLvl"] + raid_names
    rows = []
    for char_name, item_level in character_names:
        row = [char_name, item_level]
        for raid in raid_names:
            row.append(char_raid_status.get((char_name, raid), "➖"))
        rows.append(row)

    # 8. Column widths
    num_cols = len(header)
    all_table = [header] + rows
    col_widths = [
        max(len(str(row[i])) for row in all_table) + 2
        for i in range(num_cols)
    ]

    # 9. Print
    separator = "-" * sum(col_widths)

    print(f"\n{'=' * 60}")
    print("  RAID LOGS - WEEKLY COMPLETION STATUS")
    print(f"  Reset window: {reset_time.strftime('%Y-%m-%d %H:%M')} to {now_pacific.strftime('%Y-%m-%d %H:%M')} Pacific")
    print(f"{'=' * 60}\n")

    print(separator)
    header_line = " ".join(f"{header[i]:<{col_widths[i]}}" for i in range(num_cols))
    print(header_line)
    print(separator)

    for row in rows:
        row_line = " ".join(f"{row[i]:<{col_widths[i]}}" for i in range(num_cols))
        print(row_line)

    print(separator)

    # Summary
    completed_count = sum(1 for v in char_raid_status.values() if v == "✅")
    total_cells = len(character_names) * len(raid_names)
    print(f"\nCompleted this week: {completed_count}/{total_cells} (across {len(character_names)} characters, {len(raid_names)} raids)")


if __name__ == "__main__":
    character = input("Enter character name: ").strip()
    if character:
        character = character[0].upper() + character[1:].lower()
        print_raid_logs(character)
    else:
        print("No character name provided. Exiting.")
