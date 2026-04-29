import streamlit as st
import io
import sys
from uwu4 import print_raid_logs, fetch_page, extract_roster, extract_logs, _fetch_boss_to_raid, get_last_weekly_reset, _format_ilvl
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from zoneinfo import ZoneInfo
import re

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

ACCENT_MAP = {
    'a': ['à', 'á', 'â', 'ä', 'ã'],
    'e': ['è', 'é', 'ê', 'ë'],
    'i': ['ì', 'í', 'î', 'ï'],
    'o': ['ò', 'ó', 'ô', 'ö', 'õ'],
    'u': ['ù', 'ú', 'û', 'ü'],
}


def generate_accent_variants(name):
    """Generate single-vowel accent substitutions for a name."""
    variants = set()
    for i, ch in enumerate(name.lower()):
        if ch in ACCENT_MAP:
            for acc in ACCENT_MAP[ch]:
                variants.add(name[:i] + acc + name[i + 1:])
    return list(variants)


@st.cache_data(ttl=3600)
def get_boss_mapping():
    return _fetch_boss_to_raid()


@st.cache_data(ttl=300)
def get_roster(main_character):
    html = fetch_page(f"/character/NA/{quote(main_character)}/roster")
    if not html:
        return []
    return extract_roster(html)


@st.cache_data(ttl=300)
def get_character_logs(char_name, boss_to_raid):
    from uwu4 import _fetch_character_logs
    return _fetch_character_logs(char_name, boss_to_raid)


def try_accent_variants(character):
    """Try accent variants for a character name. Returns list of (name, roster) with matches."""
    variants = generate_accent_variants(character)
    if not variants:
        return []
    found = []
    with ThreadPoolExecutor(max_workers=min(len(variants), 8)) as executor:
        futures = {executor.submit(get_roster, v): v for v in variants}
        for future in as_completed(futures):
            vname = futures[future]
            vroster = future.result()
            if vroster:
                found.append((vname, vroster))
    return found


def build_table(main_character, roster=None, _skip_no_logs=False):
    boss_to_raid, raid_order = get_boss_mapping()
    if not boss_to_raid:
        st.error("Failed to fetch raid mapping from lostark.bible.")
        return None

    if roster is None:
        roster = get_roster(main_character)
    character_names = roster
    if not character_names:
        st.error(f"No characters found for **{main_character}**.")
        return None

    # Fetch all character logs in parallel
    all_entries = []
    with ThreadPoolExecutor(max_workers=min(len(character_names), 8)) as executor:
        futures = {
            executor.submit(get_character_logs, char_name, boss_to_raid): char_name
            for char_name, _ in character_names
        }
        for future in as_completed(futures):
            _, entries, _, _ = future.result()
            all_entries.extend(entries)

    if not all_entries:
        if _skip_no_logs:
            return None
        st.warning("No log entries found for any character.")
        return None

    reset_time = get_last_weekly_reset()
    now_pacific = datetime.now(PACIFIC_TZ)
    three_weeks_ago = now_pacific - timedelta(weeks=3)

    # Filter raids active in last 3 weeks, ordered by raid_order
    newest_per_raid = {}
    for entry in all_entries:
        raid = entry["raid_name"]
        if raid not in newest_per_raid or entry["completion_dt"] > newest_per_raid[raid]:
            newest_per_raid[raid] = entry["completion_dt"]
    raids_in_data = {r for r, dt in newest_per_raid.items() if dt >= three_weeks_ago}
    raid_names = [r for r in raid_order if r in raids_in_data]

    # Sort characters by ilvl desc, then alphabetical
    character_names.sort(key=lambda c: (-float(c[1]), c[0]))

    # Build status lookup
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
        if len(done) == len(char_raid_gates[key]):
            char_raid_status[key] = "✅"
        else:
            char_raid_status[key] = "❌"

    completed_count = sum(1 for v in char_raid_status.values() if v == "✅")

    return {
        "character_names": character_names,
        "raid_names": raid_names,
        "char_raid_status": char_raid_status,
        "reset_time": reset_time,
        "now_pacific": now_pacific,
        "total_chars": len(character_names),
        "total_raids": len(raid_names),
        "completed": completed_count,
        "total_cells": len(character_names) * len(raid_names),
    }


st.set_page_config(page_title="LOA Raid Tracker", page_icon="⚔️", layout="wide")
st.title("⚔️ Lost Ark Raid Tracker")

st.markdown(
    "Enter your main character name to view weekly raid completion status "
    "for all characters on your roster."
)

col_text, col_btn = st.columns([5, 1])
with col_text:
    character = st.text_input("Enter your character name. MUST HAVE PUBLIC LOGS ENABLED.")
with col_btn:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    search_clicked = st.button("Search", type="primary")

if (character and st.session_state.get("last_search") != character) or (search_clicked and character):
    # Clear stale state from previous searches
    st.session_state.pop("accent_matches", None)
    st.session_state.pop("accent_data", None)
    st.session_state["last_search"] = character
    character = character[0].upper() + character[1:].lower()

    def _handle_found(found):
        """Process accent variant matches. Always shows selection UI."""
        if found:
            st.session_state["accent_matches"] = found
            return None, None
        return None, None

    with st.spinner(f"Fetching data for {character}..."):
        resolved_name = character
        roster = get_roster(character)

        # Fallback 1: empty roster → try accent variants
        if not roster:
            found = try_accent_variants(character)
            if found:
                resolved_name, roster = _handle_found(found)
            else:
                st.error(f"No characters found for **{character}**.")
                resolved_name = None

    # Try building table (only if we have a roster)
    data = None
    if resolved_name and roster:
        data = build_table(resolved_name, roster=roster)

    # Fallback 2: roster exists but no log entries → try accent variants
    if resolved_name and roster and data is None and not st.session_state.get("accent_matches"):
        found = try_accent_variants(character)
        if found:
            resolved_name, roster = _handle_found(found)
            if resolved_name:
                data = build_table(resolved_name, roster=roster)

    if data:
        st.session_state["accent_data"] = data
    elif not st.session_state.get("accent_matches"):
        if resolved_name is None and not roster:
            pass  # already showed "no characters found" error
        else:
            st.error(f"No log entries found for **{character}** or its variants.")

# Handle multiple accent matches (persists across reruns until user picks)
if st.session_state.get("accent_matches"):
    matches = st.session_state["accent_matches"]
    st.warning("Multiple characters match. Click one to continue:")

    for m_name, m_roster in matches:
        main_ilvl = next((ilvl for cn, ilvl in m_roster if cn == m_name), "0")
        if st.button(f"{m_name}  —  iLvl {main_ilvl}  —  {len(m_roster)} characters", key=f"accent_{m_name}"):
            del st.session_state["accent_matches"]
            st.session_state["accent_data"] = build_table(m_name, roster=m_roster)
            st.rerun()

if st.session_state.get("accent_data"):
    data = st.session_state["accent_data"]
    if data:
        st.subheader("Weekly Completion Status")
        reset = data["reset_time"]
        now = data["now_pacific"]
        st.caption(
            f"Reset window: {reset.strftime('%Y-%m-%d %H:%M')} to "
            f"{now.strftime('%Y-%m-%d %H:%M')} Pacific"
        )

        raid_names = data["raid_names"]
        header = ["Character", "iLvl"] + raid_names
        rows = []
        for char_name, item_level in data["character_names"]:
            row = [char_name, item_level] + [
                data["char_raid_status"].get((char_name, raid), "➖") for raid in raid_names
            ]
            rows.append(row)

        # Build HTML table to avoid pyarrow dependency
        html = "<table style='width:100%; border-collapse:collapse; font-size:14px;'>"
        html += "<thead><tr>"
        for h in header:
            html += f"<th style='border-bottom:2px solid #444; padding:6px 10px; text-align:left;'>{h}</th>"
        html += "</tr></thead><tbody>"
        for row in rows:
            html += "<tr>"
            for i, cell in enumerate(row):
                color = ""
                if cell == "✅":
                    color = "color:#4caf50;"
                elif cell == "❌":
                    color = "color:#f44336;"
                elif cell == "➖":
                    color = "color:#666;"
                html += f"<td style='padding:6px 10px; border-bottom:1px solid #333; {color}'>{cell}</td>"
            html += "</tr>"
        html += "</tbody></table>"
        st.markdown(html, unsafe_allow_html=True)

        st.markdown(
            f"**Completed this week:** {data['completed']}/{data['total_cells']} "
            f"(across {data['total_chars']} characters, {data['total_raids']} raids)"
        )

        st.markdown(
            "<span style='color:#4caf50'>✅</span> Completed this week &nbsp;&nbsp; "
            "<span style='color:#f44336'>❌</span> Not yet completed this week &nbsp;&nbsp; "
            "<span style='color:#666'>➖</span> No prior logs in the last 3 weeks",
            unsafe_allow_html=True,
        )

st.markdown("---")
st.caption("Data sourced from [lostark.bible](https://lostark.bible)")
