"""One-off: extract every field from lostark.bible/character/NA/Sunilkoko/logs."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter

from uwu5 import fetch_page, _fetch_boss_to_raid, _register_explicit_raids, KAZEROS_OVERRIDE

PACIFIC = ZoneInfo("America/Los_Angeles")

html = fetch_page("/character/NA/Sunilkoko/logs")
print("page length:", len(html))


def bracket(html, key):
    """Text between key:[ and its matching ]."""
    start = html.find(f"{key}:[")
    if start == -1:
        return None
    open_pos = start + len(key) + 1
    depth = 1
    for i in range(open_pos + 1, len(html)):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                return html[open_pos + 1 : i]
    return None


def obj_after(html, key):
    """Text of the {...} object following key:."""
    start = html.find(f"{key}:{{")
    if start == -1:
        return None
    i = start + len(key) + 1
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[i : j + 1]
    return None


# ---------- 1. header (character card) ----------
print("\n===== HEADER BLOCK (raw) =====")
print(obj_after(html, "header"))

# ---------- 2. route flags ----------
for flag in ["logsEnabled", "isPublic", "isOwner"]:
    m = re.search(rf"{flag}:(true|false)", html)
    print(f"{flag} = {m.group(1) if m else '?'}")

# ---------- 3. log entries, every field ----------
FIELD_RE = re.compile(
    r'(\w+):('
    r'"(?:[^"\\]|\\.)*"'
    r'|null|true|false'
    r'|\[[^\]]*\]'
    r'|[-\d.]+'
    r")"
)


def parse_entry(raw):
    d = {}
    for m in FIELD_RE.finditer(raw):
        k, v = m.group(1), m.group(2)
        if v.startswith('"'):
            d[k] = v[1:-1]
        elif v in ("null", "true", "false"):
            d[k] = {"null": None, "true": True, "false": False}[v]
        elif v.startswith("["):
            inner = v[1:-1].strip()
            d[k] = [float(x) for x in inner.split(",")] if inner else []
        else:
            d[k] = float(v) if "." in v else int(v)
    return d


logs_text = bracket(html, "logs")
entries = [parse_entry(r) for r in logs_text.split("},{")]
print(f"\n===== {len(entries)} LOG ENTRIES =====")

boss_to_raid, raid_order = _register_explicit_raids(*_fetch_boss_to_raid())


def raid_of(e):
    info = KAZEROS_OVERRIDE.get((e["boss"], e.get("difficulty"))) or boss_to_raid.get(e["boss"])
    return (info[0], info[1]) if info else ("UNMAPPED", 0)


def pct(v):
    return f"{round(v * 100)}" if isinstance(v, float) else str(v)


keys_seen = []
for e in entries:
    for k in e:
        if k not in keys_seen:
            keys_seen.append(k)
print("fields present:", keys_seen)

def fmt(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and 0 < v < 1:
        return f"{v:.4f} ({round(v * 100)})"
    if isinstance(v, int) and abs(v) > 100000:
        return f"{v:,}"
    if isinstance(v, list):
        return str([round(x, 3) if isinstance(x, float) else x for x in v])
    return str(v)


for i, e in enumerate(entries, 1):
    dt = datetime.fromtimestamp(e["timestamp"] / 1000, tz=PACIFIC)
    raid, gate = raid_of(e)
    dur = e.get("duration", 0) / 1000
    print(f"\n--- entry {i} ---")
    print(f"  run id         : {e.get('id')}  (https://lostark.bible/logs/{e.get('id')})")
    print(f"  character      : {e.get('name')} ({e.get('class')} / {e.get('spec')})")
    print(f"  boss           : {e.get('boss')}")
    print(f"  -> raid / gate : {raid} G{gate}   difficulty: {e.get('difficulty')}")
    print(f"  duration       : {int(dur // 60)}:{int(dur % 60):02d} ({e.get('duration')} ms)")
    print(f"  cleared        : {dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    for k in e:
        if k in ("id", "name", "boss", "difficulty", "duration", "timestamp"):
            continue
        print(f"  {k:<22}: {fmt(e[k])}")

# ---------- 4. summary ----------
print("\n===== SUMMARY =====")
print("total entries:", len(entries))
print("distinct bosses:", len({e["boss"] for e in entries}))
print("difficulty counts:", dict(Counter(e.get("difficulty") for e in entries)))
print("raid counts:", dict(Counter(raid_of(e)[0] for e in entries)))
print("percentile null:", sum(1 for e in entries if e.get("percentile") is None))
ts = sorted(e["timestamp"] for e in entries)
print("oldest log:", datetime.fromtimestamp(ts[0] / 1000, tz=PACIFIC).strftime("%Y-%m-%d"))
print("newest log:", datetime.fromtimestamp(ts[-1] / 1000, tz=PACIFIC).strftime("%Y-%m-%d"))
