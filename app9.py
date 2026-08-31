# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "extra-streamlit-components",
#     "requests",
#     "streamlit",
#     "streamlit-local-storage",
# ]
# ///
import streamlit as st
import json
from pathlib import Path
from uwu9 import print_raid_logs, fetch_page, extract_roster, extract_logs, _fetch_boss_to_raid, get_last_weekly_reset, _format_ilvl, _register_explicit_raids
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from zoneinfo import ZoneInfo
from streamlit_local_storage import LocalStorage
import re

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")

ACCENT_MAP = {
    'a': ['à', 'á', 'â', 'ä', 'ã'],
    'e': ['è', 'é', 'ê', 'ë'],
    'i': ['ì', 'í', 'î', 'ï'],
    'o': ['ò', 'ó', 'ô', 'ö', 'õ'],
    'u': ['ù', 'ú', 'û', 'ü'],
}

# --- Pinned rosters (browser localStorage — works on Community Cloud) ---
local_storage = LocalStorage()
PINNED_KEY = "loa_pinned_rosters"
MAX_PINS = 10

def raid_column_width(name, num_gates=1, char_px=8, pad_px=12, floor_px=60, gate_px=38):
    """Pick a column width (CSS px) for a raid header so it fits without
    clipping, wrapping multi-word names onto multiple lines.

    Each raid cell holds one marker *per gate* (a small gate number plus either
    a status emoji or a green percentile number), so the column must be wide
    enough for ``num_gates`` markers in a row as well as for the longest word in
    the header. We take the max of the header-based estimate, the gate-based
    estimate, and the floor. char_px is a generous per-character estimate at
    font-size 14px; gate_px is a per-marker estimate (gate label + emoji or
    up-to-3-digit percentile + spacing). This is a heuristic — Python can't
    measure the browser's rendered text width, and cells use ``white-space:nowrap``
    so they grow rather than clip if the estimate is low.
    """
    longest = max(name.split(), key=len)
    header_w = len(longest) * char_px + pad_px
    gates_w = num_gates * gate_px + pad_px
    return f"{max(header_w, gates_w, floor_px)}px"


def _percentile_color(n):
    """Text color for a displayed percentile, matching lostark.bible's badge tiers.

    Copied from the site's badge component (the tier function over the floored
    percentile in its logs-page JS chunk): 100 gold, 99 pink, 95+ orange,
    75+ purple, 50+ blue, 25+ green, else grey.
    """
    if n >= 100:
        return "#dcc999"
    if n >= 99:
        return "#FF69B4"
    if n >= 95:
        return "#FFA441"
    if n >= 75:
        return "#ce84ff"
    if n >= 50:
        return "#0096ff"
    if n >= 25:
        return "#3dd351"
    return "#afafaf"


# Class icons extracted from lostark.bible's bundled class-icon component
# (inline SVGs — the site has no image URLs for them; see _class_icons/).
# One <id>.svg per internal class id, keyed by the raw roster class_id.
CLASS_ICON_DIR = Path(__file__).parent / "_class_icons"


@st.cache_data
def get_class_icon_svg(class_id, size=18):
    """Return the class icon as an inline <svg> string, sized for the table.

    The SVGs use fill="currentColor", so they inherit the cell's text color.
    Falls back to "" for missing/unknown classes — callers then show the class
    name text instead.
    """
    if not class_id:
        return ""
    try:
        svg = (CLASS_ICON_DIR / f"{class_id}.svg").read_text(encoding="utf-8")
    except OSError:
        return ""
    # Raw templates carry only a viewBox; give an explicit size so they don't
    # blow up the row height.
    return svg.replace(
        "<svg ", f'<svg width="{size}" height="{size}" style="vertical-align:middle;" ', 1
    )


def load_pinned():
    """Load pinned roster names from browser localStorage."""
    raw = local_storage.getItem(itemKey=PINNED_KEY)
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def save_pinned(names):
    """Queue a save to browser localStorage. Runs on the NEXT script rerun."""
    st.session_state["_pending_pin_save"] = names


def _flush_pending_save():
    """Actually write pending data to localStorage.

    This MUST run as part of a normal (uninterrupted) script execution so the
    setItem component's JavaScript has time to execute in the browser.  It is
    called near the TOP of the script, before any st.rerun() can interrupt.
    """
    names = st.session_state.pop("_pending_pin_save", None)
    if names is None:
        return
    counter = st.session_state.get("_pin_counter", 0) + 1
    st.session_state["_pin_counter"] = counter
    value = json.dumps(names, ensure_ascii=False)
    local_storage.setItem(
        itemKey=PINNED_KEY,
        itemValue=value,
        key=f"pin_set_{counter}",
    )
    # Sync session_state so the constructor doesn't use stale data on rerun.
    st.session_state[local_storage.storedKey][PINNED_KEY] = value


# Flush any pending save from a previous button click.
_flush_pending_save()


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
    boss_to_raid, raid_order = _fetch_boss_to_raid()
    # Layer explicitly-registered raids (e.g. Horizon Cathedral) on top of the
    # live mapping without altering any existing raid's mapping.
    return _register_explicit_raids(boss_to_raid, raid_order)


@st.cache_data(ttl=300)
def get_roster(main_character):
    html = fetch_page(f"/character/NA/{quote(main_character)}/roster")
    if not html:
        return []
    return extract_roster(html)


@st.cache_data(ttl=300)
def get_character_logs(char_name, boss_to_raid):
    from uwu9 import _fetch_character_logs
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
            try:
                vroster = future.result()
                if vroster:
                    found.append((vname, vroster))
            except Exception:
                pass
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

    # Total gates per raid = the highest gate number mapped to that raid.
    raid_total_gates = {}
    for _boss, (raid, gate) in boss_to_raid.items():
        if gate > raid_total_gates.get(raid, 0):
            raid_total_gates[raid] = gate

    # Fetch all character logs in parallel
    all_entries = []
    with ThreadPoolExecutor(max_workers=min(len(character_names), 8)) as executor:
        futures = {
            executor.submit(get_character_logs, char_name, boss_to_raid): char_name
            for char_name, _ilvl, _cls, _cid in character_names
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

    # Build per-GATE completion status.
    #
    # Lost Ark gates are sequential within a weekly lockout: you cannot clear
    # gate K without first clearing gates 1..K that same week. So rather than
    # trust each gate's log entry individually (lostark.bible sometimes only
    # logs the highest gate cleared in a period — see commit 68db827), we record
    # the highest gate cleared *ever* and *inside the window* per (character,
    # raid) and treat every gate up to that high-water mark as cleared. Gates
    # above the all-time high-water mark were never reached (➖).
    gates_ever = {}  # (character, raid) -> set of gate numbers ever logged
    for entry in all_entries:
        gates_ever.setdefault(
            (entry["character"], entry["raid_name"]), set()
        ).add(entry["gate_number"])

    def _window_view(window_start, window_end=None):
        """Statuses, percentiles and completion count for ONE weekly window.

        window_end=None means "up to now" (the current reset week); otherwise
        the window is [window_start, window_end). Everything is derived from
        the same all_entries fetch, so switching windows in the UI is free.
        """
        gates_in_window = {}  # (character, raid) -> gate numbers logged in window
        for entry in all_entries:
            dt = entry["completion_dt"]
            if dt >= window_start and (window_end is None or dt < window_end):
                gates_in_window.setdefault(
                    (entry["character"], entry["raid_name"]), set()
                ).add(entry["gate_number"])

        gate_status = {}  # (character, raid) -> {gate_number: "✅" | "❌" | "➖"}
        for key, ever in gates_ever.items():
            total = raid_total_gates.get(key[1], 0)
            if total == 0:
                continue  # unmapped raid; these never appear as columns (not in raid_order)
            max_ever = max(ever) if ever else 0
            in_window = gates_in_window.get(key, set())
            max_in_window = max(in_window) if in_window else 0
            statuses = {}
            for gate in range(1, total + 1):
                if gate <= max_in_window:
                    statuses[gate] = "✅"
                elif gate <= max_ever:
                    statuses[gate] = "❌"
                else:
                    statuses[gate] = "➖"
            gate_status[key] = statuses

        # Performance percentile per gate cleared inside the window, taken from
        # that gate's own log entry (0-1 float + which metric it is:
        # "contribution" for supports, "damage" for DPS). Gates marked ✅ via
        # the high-water-mark rule (no log entry of their own in the window)
        # and entries whose percentile is null stay out of this map — those
        # keep the plain ✅ in the table.
        gate_pct = {}  # (character, raid, gate) -> (percentile, percentile_kind, log_id)
        gate_log = {}  # (character, raid, gate) -> log_id of newest in-window entry
        for entry in all_entries:
            dt = entry["completion_dt"]
            if dt >= window_start and (window_end is None or dt < window_end):
                key = (entry["character"], entry["raid_name"], entry["gate_number"])
                # The site lists newest first; keep the newest in-window value.
                # gate_pct keeps the newest entry WITH a percentile — that's
                # the run whose number the table shows, so its log_id is the
                # link target for the number. gate_log keeps the newest entry
                # of any kind — the ✅-no-percentile cells link there.
                if entry["log_id"]:
                    gate_log.setdefault(key, entry["log_id"])
                if entry["percentile"] is not None:
                    gate_pct.setdefault(
                        key,
                        (entry["percentile"], entry["percentile_kind"], entry["log_id"]),
                    )

        # A raid counts as completed in the window only when every gate is cleared.
        completed = sum(
            1 for statuses in gate_status.values()
            if statuses and all(s == "✅" for s in statuses.values())
        )
        return {
            "char_raid_gate_status": gate_status,
            "char_raid_gate_pct": gate_pct,
            "char_raid_gate_log": gate_log,
            "completed": completed,
        }

    # The current reset week (shown by default) and the week before it, so the
    # UI can switch between them without refetching anything.
    current_view = _window_view(reset_time)
    prev_reset_time = reset_time - timedelta(weeks=1)
    prev_view = _window_view(prev_reset_time, reset_time)

    return {
        "character_names": character_names,
        "raid_names": raid_names,
        "char_raid_gate_status": current_view["char_raid_gate_status"],
        "char_raid_gate_pct": current_view["char_raid_gate_pct"],
        "char_raid_gate_log": current_view["char_raid_gate_log"],
        "raid_total_gates": raid_total_gates,
        "reset_time": reset_time,
        "prev_reset_time": prev_reset_time,
        "prev": prev_view,
        "now_pacific": now_pacific,
        "total_chars": len(character_names),
        "total_raids": len(raid_names),
        "completed": current_view["completed"],
        "total_cells": len(character_names) * len(raid_names),
    }


st.set_page_config(page_title="LOA Raid Tracker", page_icon="⚔️", layout="wide")
st.title("⚔️ Lost Ark Raid Tracker")

st.markdown(
    "Enter your main character name to view weekly raid completion status "
    "for all characters on your roster."
)

# --- Pinned rosters ---
pinned = load_pinned()
if pinned:
    st.markdown("##### 📌 Pinned Rosters")
    pin_cols = st.columns(min(len(pinned), 5))
    for i, name in enumerate(pinned):
        with pin_cols[i % 5]:
            col_btn, col_x = st.columns([4, 1])
            with col_btn:
                if st.button(name, key=f"pin_{name}", use_container_width=True):
                    st.session_state["_pin_search"] = name
                    st.rerun()
            with col_x:
                if st.button("✖", key=f"unpin_{name}", help="Unpin"):
                    pinned = [n for n in pinned if n.lower() != name.lower()]
                    save_pinned(pinned)
                    st.rerun()

with st.form("search_form", clear_on_submit=False):
    col_text, col_btn = st.columns([5, 1])
    with col_text:
        character = st.text_input("Enter your character name. MUST HAVE PUBLIC LOGS ENABLED.")
    with col_btn:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        search_clicked = st.form_submit_button("Search", type="primary")

# --- Auto-search from pinned roster click ---
if st.session_state.get("_pin_search"):
    pin_name = st.session_state.pop("_pin_search")
    st.session_state.pop("accent_matches", None)
    st.session_state.pop("accent_data", None)
    # A new roster is loading — reset the previous-reset toggle to default.
    # Set (not pop): a pop only resets the script-side value; the browser's
    # switch stays flipped ON and re-syncs its old state on the next rerun.
    st.session_state["_show_previous_reset"] = False

    with st.spinner(f"Fetching data for {pin_name}..."):
        roster = get_roster(pin_name)
        data = None
        resolved_name = pin_name
        if roster:
            data = build_table(pin_name, roster=roster)
        else:
            st.error(f"No characters found for **{pin_name}**.")
            resolved_name = None

    if data:
        st.session_state["accent_data"] = data
        st.session_state["_resolved_name"] = resolved_name

if search_clicked and character:
    # Clear stale state from previous searches
    st.session_state.pop("accent_matches", None)
    st.session_state.pop("accent_data", None)
    # A new roster is loading — reset the previous-reset toggle to default
    # (set, not pop — see the pinned-roster block for why).
    st.session_state["_show_previous_reset"] = False
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
        st.session_state["_resolved_name"] = resolved_name
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
        main_ilvl = next((ilvl for cn, ilvl, _cls, _cid in m_roster if cn == m_name), "0")
        if st.button(f"{m_name}  —  iLvl {main_ilvl}  —  {len(m_roster)} characters", key=f"accent_{m_name}"):
            del st.session_state["accent_matches"]
            # A new roster is loading — reset the previous-reset toggle to default
            # (set, not pop — see the pinned-roster block for why).
            st.session_state["_show_previous_reset"] = False
            st.session_state["accent_data"] = build_table(m_name, roster=m_roster)
            st.session_state["_resolved_name"] = m_name
            st.rerun()

if st.session_state.get("accent_data"):
    data = st.session_state["accent_data"]
    if data:
        st.subheader("Weekly Completion Status")

        # Current reset week by default; flip the switch to view the week
        # before it. Both windows were precomputed in build_table over the same
        # fetched entries, so toggling is instant — no refetch, no rebuild.
        show_previous = st.toggle(
            "Show previous reset",
            value=False,
            key="_show_previous_reset",
            help="Switch the matrix to the weekly-reset window before the current one.",
        )
        view = data["prev"] if show_previous else data
        # Wording that tracks the selected window: "this reset"/"previous reset"
        # for clears, and a miss label that stays accurate for the previous
        # window (a gate not cleared then may have been cleared before OR after it).
        reset_short = "previous reset" if show_previous else "this reset"
        miss_label = "cleared in a different reset" if show_previous else "cleared before this reset"

        window_start = data["prev_reset_time"] if show_previous else data["reset_time"]
        window_end = data["reset_time"] if show_previous else data["now_pacific"]
        st.caption(
            f"Reset window: {window_start.strftime('%Y-%m-%d %H:%M')} to "
            f"{window_end.strftime('%Y-%m-%d %H:%M')} Pacific"
        )

        raid_names = data["raid_names"]
        raid_total_gates = data["raid_total_gates"]
        gate_status = view["char_raid_gate_status"]
        gate_pct = view["char_raid_gate_pct"]
        gate_log = view["char_raid_gate_log"]

        def _gate_icon(status, gate, pct=None, pct_kind=None, log_id=None):
            """Render a single gate's number + status marker with a hover tooltip.

            A gate cleared in the selected reset window with a non-null performance
            percentile shows that percentile as a number instead of the ✅ emoji —
            the same value lostark.bible badges for the log (damage percentile for
            DPS, contribution percentile for supports), colored with the site's own
            badge tier colors (see _percentile_color). The small gate-number prefix
            keeps its status color (green here) regardless of the tier color. A
            null percentile or a gate marked cleared without its own log entry
            keeps the ✅. Tooltip wording (reset_short / miss_label) tracks the
            window currently shown.

            Percentile numbers and ✅ markers link to the run's lostark.bible page
            (/logs/{id}) when the log entry is known — the number links to the
            exact run whose percentile it shows, the ✅ to the gate's newest
            in-window run. The gate-number prefix never links; ❌/➖ markers have
            no single run to point at and stay plain.
            """
            if status == "✅" and pct is not None:
                # The site floors the percentile for its badge (Math.floor in its
                # badge component), so floor here too — rounding could land a
                # 99.5th on the wrong side of the 99/100 tier boundary.
                n = int(pct * 100)
                kind_txt = " (contribution)" if pct_kind == "contribution" else ""
                label = f"Gate {gate}: cleared {reset_short} — {n}th percentile{kind_txt}"
                if log_id:
                    n_html = (
                        f"<a class='log-link' href='https://lostark.bible/logs/{log_id}' "
                        f"target='_blank' rel='noopener noreferrer' "
                        f"style='color:{_percentile_color(n)}; font-weight:600;'>{n}</a>"
                    )
                else:
                    n_html = f"<span style='color:{_percentile_color(n)}; font-weight:600;'>{n}</span>"
                return (
                    f"<span title='{label}' "
                    f"style='display:inline-block; margin-right:5px;'>"
                    f"<span style='font-size:10px; opacity:.6; margin-right:3px; color:#4caf50;'>{gate}</span>{n_html}"
                    f"</span>"
                )
            if status == "✅":
                label, color = f"Gate {gate}: cleared {reset_short}", "#4caf50"
            elif status == "❌":
                label, color = f"Gate {gate}: {miss_label}", "#f44336"
            else:
                label, color = f"Gate {gate}: not cleared", "#666"
            marker = status
            if status == "✅" and log_id:
                marker = (
                    f"<a class='log-link' href='https://lostark.bible/logs/{log_id}' "
                    f"target='_blank' rel='noopener noreferrer' "
                    f"style='color:{color};'>{status}</a>"
                )
            return (
                f"<span title='{label}' "
                f"style='display:inline-block; margin-right:5px; color:{color};'>"
                f"<span style='font-size:10px; opacity:.6;'>{gate}</span>{marker}"
                f"</span>"
            )

        def _gate_cell(char_name, raid):
            """All gate markers for one (character, raid), ordered G1..GN."""
            total = raid_total_gates.get(raid, 1)
            statuses = gate_status.get((char_name, raid))
            icons = []
            for gate in range(1, total + 1):
                status = statuses.get(gate, "➖") if statuses else "➖"
                pct, pct_kind, pct_log = gate_pct.get(
                    (char_name, raid, gate), (None, None, None)
                )
                # A numeric cell links to the exact run whose percentile it
                # shows (pct_log); a plain ✅ falls back to the gate's newest
                # in-window entry.
                log_id = pct_log or gate_log.get((char_name, raid, gate))
                icons.append(_gate_icon(status, gate, pct, pct_kind, log_id))
            return "".join(icons)

        # Dynamically size each raid column to fit its per-gate icons AND its
        # header (multi-word names still wrap). Cells use white-space:nowrap so
        # the gates stay on one line; the column grows rather than clips if this
        # heuristic underestimates. Character/Class/iLvl stay on auto sizing.
        # Raid columns start at index 3 (after "Character", "Class" and "iLvl").
        raid_col_widths = {
            i + 3: raid_column_width(r, raid_total_gates.get(r, 1))
            for i, r in enumerate(raid_names)
        }

        # Build HTML table to avoid pyarrow dependency.
        # NOTE: no width:100% — with explicit raid column widths set below,
        # forcing 100% would dump all the slack into the unpinned Character /
        # iLvl columns and balloon them. Letting the table shrink to content
        # keeps those columns compact.
        header = ["Character", "Class", "iLvl"] + raid_names
        html = "<table style='border-collapse:collapse; font-size:14px;'>"
        if raid_col_widths:
            html += "<colgroup>"
            for i in range(len(header)):
                w = raid_col_widths.get(i)
                html += f"<col{f' style=\"width:{w};\"' if w else ''}>"
            html += "</colgroup>"
        html += "<thead><tr>"
        for h in header:
            html += f"<th style='border-bottom:2px solid #444; padding:6px 10px; text-align:left;'>{h}</th>"
        html += "</tr></thead><tbody>"
        for char_name, item_level, char_class, class_id in data["character_names"]:
            html += "<tr>"
            char_url = f"https://lostark.bible/character/NA/{quote(char_name)}/logs"
            html += (
                f"<td style='padding:6px 10px; border-bottom:1px solid #333;'>"
                f"<a href='{char_url}' target='_blank' rel='noopener' "
                f"style='color:#8ab4f8; text-decoration:none;'>{char_name}</a></td>"
            )
            # Class icon (hover shows the class name); text fallback when the
            # class has no extracted icon (e.g. a brand-new class id).
            icon_svg = get_class_icon_svg(class_id)
            class_cell = (
                f"<span title='{char_class}'>{icon_svg}</span>" if icon_svg else char_class
            )
            html += (
                f"<td style='padding:6px 10px; border-bottom:1px solid #333; white-space:nowrap;'>"
                f"{class_cell}</td>"
            )
            html += f"<td style='padding:6px 10px; border-bottom:1px solid #333;'>{item_level}</td>"
            for raid in raid_names:
                html += (
                    f"<td style='padding:6px 10px; border-bottom:1px solid #333; white-space:nowrap;'>"
                    f"{_gate_cell(char_name, raid)}</td>"
                )
            html += "</tr>"
        html += "</tbody></table>"
        # Styling for the log links. The no-underline must live HERE, not inline
        # on the anchors — an inline text-decoration would out-rank this sheet's
        # :hover rule (inline beats selectors) and the hover could never apply.
        html += (
            "<style>a.log-link{text-decoration:none}"
            "a.log-link:hover{text-decoration:underline}</style>"
        )
        st.markdown(html, unsafe_allow_html=True)

        st.markdown(
            f"**Completed {reset_short}:** {view['completed']}/{data['total_cells']} "
            f"(across {data['total_chars']} characters, {data['total_raids']} raids)"
        )

        st.markdown(
            "Each raid cell shows one marker per gate in order (G1, G2, …). "
            "A green number is that gate's performance percentile from lostark.bible "
            "(damage for DPS, contribution for supports); hover any marker for details.",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<span style='color:{_percentile_color(95)}; font-weight:600;'>95</span> Gate cleared {reset_short} w/ percentile<br>"
            f"<span style='color:#4caf50'>✅</span> Gate cleared {reset_short}, no percentile<br>"
            f"<span style='color:#f44336'>❌</span> Gate not cleared {reset_short}<br>"
            f"<span style='color:#666'>➖</span> No logs for this gate",
            unsafe_allow_html=True,
        )

        # --- Pin / Unpin button ---
        resolved = st.session_state.get("_resolved_name", "")
        current_pinned = load_pinned()
        is_pinned = any(p.lower() == resolved.lower() for p in current_pinned)

        if is_pinned:
            if st.button("✖ Unpin roster", key="unpin_result"):
                current_pinned = [p for p in current_pinned if p.lower() != resolved.lower()]
                save_pinned(current_pinned)
                st.rerun()
        else:
            if st.button("📌 Pin this roster", key="pin_result"):
                if len(current_pinned) >= MAX_PINS:
                    st.warning(f"Maximum of {MAX_PINS} pinned rosters reached. Unpin one first.")
                else:
                    current_pinned.append(resolved)
                    save_pinned(current_pinned)
                    st.rerun()

st.markdown("---")
st.caption("Data sourced from [lostark.bible](https://lostark.bible)")
