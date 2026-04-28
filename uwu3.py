import requests
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

BASE_URL = "https://lostark.bible"
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


def _fetch_boss_to_raid():
    """Fetch the boss->raid mapping from lostark.bible's SvelteKit JS.

    The mapping lives in the node 18 component (the logs page route).
    We discover its hashed filename by parsing the app entry JS.
    """
    # 1. Get any page to find the app entry JS filename
    resp = requests.get(f"{BASE_URL}/character/NA/test/logs", headers=HEADERS, timeout=60)
    resp.encoding = "utf-8"
    app_match = re.search(r'entry/app\.([^"]+\.js)', resp.text)
    if not app_match:
        return {}
    app_url = f"{BASE_URL}/_app/immutable/entry/app.{app_match.group(1)}"

    # 2. Fetch the app JS and find node 18's filename
    app_js = requests.get(app_url, headers=HEADERS, timeout=60).text
    node18_match = re.search(r'nodes/18\.([^"]+\.js)', app_js)
    if not node18_match:
        return {}
    node18_url = f"{BASE_URL}/_app/immutable/nodes/18.{node18_match.group(1)}"

    # 3. Fetch node 18 and parse the raid mapping
    node18_js = requests.get(node18_url, headers=HEADERS, timeout=60).text

    # Find the mapping: var P={RaidName:[`Boss1`,`Boss2`],...}
    start = node18_js.find("={")
    if start == -1:
        return {}
    start += 1  # position of '{'
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


BOSS_TO_RAID, RAID_ORDER = _fetch_boss_to_raid()


def get_last_weekly_reset():
    """Return the datetime of the most recent Wednesday 3AM Pacific (Lost Ark weekly reset)."""
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
    open_pos = start + len(key) + 1  # position of '['
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


def fetch_page(path):
    """Fetch HTML from lostark.bible with retry."""
    url = f"{BASE_URL}{path}"
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60)
            resp.encoding = "utf-8"
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            if attempt < 2:
                print(f"    ⚠ Request failed, retrying ({attempt + 2}/3): {e}")
                time.sleep(2)
            else:
                print(f"    ⚠ Failed after 3 attempts: {e}")
                return ""


def extract_roster(html):
    """Extract (name, ilvl_display) pairs from the roster SvelteKit data."""
    roster_text = _extract_bracket_text(html, "roster")
    if not roster_text:
        return []
    characters = []
    for m in re.finditer(r'name:"([^"]+)"[^}]*?ilvl:([\d.]+)', roster_text):
        characters.append((m.group(1), _format_ilvl(m.group(2))))
    return characters


def extract_logs(html):
    """Extract log entries from the logs SvelteKit data. Each entry has boss, timestamp, completion_dt."""
    logs_text = _extract_bracket_text(html, "logs")
    if not logs_text:
        return []
    bosses = re.findall(r'boss:"([^"]+)"', logs_text)
    timestamps = re.findall(r"timestamp:(\d+)", logs_text)
    entries = []
    for boss, ts in zip(bosses, timestamps):
        ts_ms = int(ts)
        raid_info = BOSS_TO_RAID.get(boss)
        entries.append({
            "boss": boss,
            "raid_name": raid_info[0] if raid_info else boss,
            "gate_number": raid_info[1] if raid_info else 0,
            "completion_dt": datetime.fromtimestamp(ts_ms / 1000, tz=PACIFIC_TZ),
        })
    return entries


def print_raid_logs(main_character):
    print(f"Fetching data for {main_character}...\n")

    # 1. Get roster
    print(f"Fetching roster for {main_character}...")
    roster_html = fetch_page(f"/character/NA/{main_character}/roster")
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

    # 3. Fetch logs for each character
    all_entries = []
    for char_name, _ in character_names:
        print(f"  Fetching logs for {char_name}...")
        logs_html = fetch_page(f"/character/NA/{quote(char_name)}/logs")
        if not logs_html:
            print(f"    -> Failed to load logs.")
            continue
        entries = extract_logs(logs_html)
        if not entries:
            print(f"    -> No log entries found.")
            continue

        # Keep only the most recent log per boss (logs are ordered most recent first)
        seen_bosses = set()
        for entry in entries:
            if entry["boss"] not in seen_bosses:
                seen_bosses.add(entry["boss"])
                entry["character"] = char_name
                all_entries.append(entry)

        print(f"    -> {len(entries)} logs, {len(seen_bosses)} unique bosses")

    if not all_entries:
        print("\nNo log entries found for any character.")
        return

    # 4. Collect unique raid names, ordered by RAID_ORDER (newest first)
    # Filter out raids whose newest log across all characters is 3+ weeks old
    three_weeks_ago = now_pacific - timedelta(weeks=3)
    newest_per_raid = {}
    for entry in all_entries:
        raid = entry["raid_name"]
        if raid not in newest_per_raid or entry["completion_dt"] > newest_per_raid[raid]:
            newest_per_raid[raid] = entry["completion_dt"]
    raids_in_data = {r for r, dt in newest_per_raid.items() if dt >= three_weeks_ago}
    raid_names = [r for r in RAID_ORDER if r in raids_in_data]

    # 5. Sort characters by ilvl descending
    character_names.sort(key=lambda c: (-float(c[1]), c[0]))

    # 6. Build completion status lookup: (character, raid) -> done/miss/na
    char_raid_status = {}
    for entry in all_entries:
        key = (entry["character"], entry["raid_name"])
        if key not in char_raid_status:
            dt = entry["completion_dt"]
            char_raid_status[key] = "✅" if dt >= reset_time else "❌"

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

    # 8. Print
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
    unique_completed = {
        (e["character"], e["raid_name"])
        for e in all_entries
        if e["completion_dt"] >= reset_time
    }
    total_cells = len(character_names) * len(raid_names)
    print(f"\nCompleted this week: {len(unique_completed)}/{total_cells} (across {len(character_names)} characters, {len(raid_names)} raids)")


if __name__ == "__main__":
    character = input("Enter character name: ").strip()
    if character:
        character = character[0].upper() + character[1:].lower()
        print_raid_logs(character)
    else:
        print("No character name provided. Exiting.")
