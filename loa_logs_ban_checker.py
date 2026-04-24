# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "requests>=2.33.1",
# ]
# ///
import os
import sqlite3
import requests
import sys

DB_RELATIVE_PATH = r"LOA Logs\encounters.db"
JSON_URL = "https://snow.xyz/loa-logs/bans.json"


def get_db_path():
    local_appdata = os.getenv("LOCALAPPDATA")
    if not local_appdata:
        raise EnvironmentError("LOCALAPPDATA environment variable not found.")

    db_path = os.path.join(local_appdata, DB_RELATIVE_PATH)

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at: {db_path}")

    return db_path


def fetch_ban_list():
    try:
        response = requests.get(JSON_URL, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch ban list: {e}")

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError("Failed to parse JSON from ban list.")

    if not isinstance(data, list):
        raise RuntimeError("Unexpected JSON format: expected a list of IDs.")

    # Convert all IDs to integers (important for SQLite comparison)
    try:
        return [int(x) for x in data]
    except ValueError:
        raise RuntimeError("Ban list contains non-integer values.")


def query_matches(db_path, banned_ids):
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as e:
        raise RuntimeError(f"Failed to open database: {e}")

    try:
        cursor = conn.cursor()

        # Use parameterized query with IN clause
        placeholders = ",".join("?" for _ in banned_ids)
        query = f"""
            SELECT DISTINCT character_id, name
            FROM entity
            WHERE character_id IN ({placeholders})
        """

        cursor.execute(query, banned_ids)
        results = cursor.fetchall()

    except sqlite3.Error as e:
        raise RuntimeError(f"Database query failed: {e}")
    finally:
        conn.close()

    return results


def main():
    try:
        db_path = get_db_path()
        banned_ids = fetch_ban_list()

        print(f"Loaded {len(banned_ids)} banned IDs.")

        if not banned_ids:
            print("Ban list is empty. Nothing to check.")
            return

        matches = query_matches(db_path, banned_ids)

        if not matches:
            print("No matches found in the database.")
            return

        print(f"\nFound {len(matches)} matching entries:\n")

        for character_id, name in matches:
            print(f"{character_id} -> {name}")

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()