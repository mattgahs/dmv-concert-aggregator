#!/usr/bin/env python3
"""
IMP venue adapter for the Ticketmaster Discovery API.

Usage:
    export TM_API_KEY=...
    python imp_fetch.py venues     # resolve venue IDs (run once, then paste into VENUES)
    python imp_fetch.py sync       # fetch events -> events.db

`sync` also fetches a one-time Wikipedia bio for each new headliner (via the
wiki link Ticketmaster's attraction record carries), cached in events.db so
it's never re-fetched. Stdlib only. No dependencies -- the Wikipedia REST API
needs no key either.
"""

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://app.ticketmaster.com/discovery/v2"
DB_PATH = os.environ.get("IMP_DB", "events.db")

# Fill `id` in after running `venues`. Hardcoding beats name-matching at runtime:
# there is more than one "Lincoln Theatre" in the US and you do not want to
# silently ingest the wrong one.
#
# NOTE: the `id` you want is the Discovery API id (looks like "KovZpZA6tnEA"),
# NOT the numeric id in a ticketmaster.com/venue/172718 URL. Those are legacy
# ids and the API will not accept them.
VENUES = [
    # Washington, DC — I.M.P.
    {"key": "930",          "name": "9:30 Club",                  "state": "DC", "id": "KovZpZA7knFA"},
    # Two identical-name, identical-address candidates came back for The
    # Atlantis; Z7r9jZaAdC is a stale duplicate with 0 events (checked via a
    # live events query), KovZ917AinI is the real one (85 events).
    {"key": "atlantis",     "name": "The Atlantis",               "state": "DC", "id": "KovZ917AinI"},
    {"key": "lincoln",      "name": "Lincoln Theatre",            "state": "DC", "id": "KovZpZAFk6EA"},
    {"key": "anthem",       "name": "The Anthem",                 "state": "DC", "id": "KovZ917A3Y7"},
    # Maryland
    {"key": "fillmore",     "name": "The Fillmore Silver Spring", "state": "MD", "id": "KovZpZA6tFlA"},
    {"key": "merriweather", "name": "Merriweather Post Pavilion", "state": "MD", "id": "KovZpZA1JkvA"},
    {"key": "soundstage",   "name": "Baltimore Soundstage",       "state": "MD", "id": "KovZpa4fJe"},
    {"key": "nevermore",    "name": "Nevermore Hall",             "state": "MD", "id": "KovZ917AYnv"},
    {"key": "pier6",        "name": "Pier Six Pavilion",          "state": "MD", "id": "KovZpZAa1enA"},
    {"key": "cfg",          "name": "CFG Bank Arena",             "state": "MD", "id": "KovZpZAJe6dA"},
    # Virginia
    # "Wolf Trap" alone is ambiguous on Ticketmaster: there's a second, stale
    # venue entry at the same address with zero events. Match on "Filene
    # Center" in the name to get the live one. The Barns at Wolf Trap (the
    # separate Oct-May indoor venue) is out of scope -- it isn't on TM at all.
    {"key": "wolftrap",     "name": "Wolf Trap Filene Center",    "state": "VA", "id": "KovZpZAEetJA"},
]

# Discovery `classifications[].segment.name` values we treat as shows.
# Everything else (conventions, WWE, auto shows at Merriweather/Anthem) is
# rejected -- but logged, not dropped silently, so you can see what the filter
# is actually catching before you trust it.
KEEP_SEGMENTS = {"Music"}


def get(path, **params):
    params["apikey"] = os.environ["TM_API_KEY"]
    url = f"{API}/{path}.json?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def cmd_venues():
    """Print candidate venue IDs for manual confirmation."""
    for v in VENUES:
        print(f"\n=== {v['name']} ({v['state']}) ===")
        data = get("venues", keyword=v["name"], stateCode=v["state"], countryCode="US")
        for hit in data.get("_embedded", {}).get("venues", []):
            city = hit.get("city", {}).get("name", "?")
            addr = hit.get("address", {}).get("line1", "?")
            print(f"  {hit['id']:<20} {hit['name']}  --  {addr}, {city}")
        time.sleep(0.25)  # Discovery allows ~5 req/sec
    print("\nConfirm the addresses, then paste the IDs into VENUES.")


def fetch_events(venue_id):
    """Yield raw event dicts for one venue, following pagination."""
    page = 0
    while True:
        data = get("events", venueId=venue_id, size=200, page=page, sort="date,asc")
        events = data.get("_embedded", {}).get("events", [])
        yield from events
        info = data.get("page", {})
        page += 1
        if page >= info.get("totalPages", 1) or not events:
            return
        time.sleep(0.25)


def normalize(ev, venue_key):
    """Map a Discovery event onto our row shape. Returns None if filtered out."""
    segs = {
        c.get("segment", {}).get("name")
        for c in ev.get("classifications", [])
        if c.get("segment")
    }
    if segs and not (segs & KEEP_SEGMENTS):
        print(f"  [reject] {ev.get('name')} -- segments={segs or '{}'}")
        return None

    attractions = ev.get("_embedded", {}).get("attractions", [])
    start = ev.get("dates", {}).get("start", {})

    return {
        "source_id": f"tm:{ev['id']}",
        "venue": venue_key,
        "title": ev.get("name"),
        "headliner": attractions[0]["name"] if attractions else None,
        # Ticketmaster's own attraction id -- used to fetch a bio once per act
        # and join it back at render time. Not a display field.
        "headliner_id": attractions[0]["id"] if attractions else None,
        "support": ", ".join(a["name"] for a in attractions[1:]) or None,
        # Discovery does not expose doors reliably; left null for now.
        "doors_utc": None,
        # dateTime is UTC and absent for TBA-time events; local_date is the fallback.
        "start_utc": start.get("dateTime"),
        "local_date": start.get("localDate"),
        "local_time": start.get("localTime"),
        "ticket_url": ev.get("url"),
        "on_sale_utc": ev.get("sales", {}).get("public", {}).get("startDateTime"),
        "status": ev.get("dates", {}).get("status", {}).get("code"),
    }


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    source_id   TEXT PRIMARY KEY,
    venue       TEXT NOT NULL,
    title       TEXT,
    headliner   TEXT,
    support     TEXT,
    doors_utc   TEXT,
    start_utc   TEXT,
    local_date  TEXT,
    local_time  TEXT,
    ticket_url  TEXT,
    on_sale_utc TEXT,
    status      TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(local_date);

-- One row per Ticketmaster attraction (roughly: act), fetched once and
-- cached forever -- bios don't change often enough to justify re-fetching on
-- every sync, and most acts have no Wikipedia page at all, so retrying those
-- daily would just be wasted calls.
CREATE TABLE IF NOT EXISTS attractions (
    attraction_id TEXT PRIMARY KEY,
    name          TEXT,
    wiki_url      TEXT,
    bio           TEXT,
    checked_at    TEXT NOT NULL
);
"""


def ensure_schema(db):
    db.executescript(SCHEMA)
    # events predates headliner_id; CREATE TABLE IF NOT EXISTS won't add it
    # to a DB that already exists, so migrate it in by hand.
    cols = {row[1] for row in db.execute("PRAGMA table_info(events)")}
    if "headliner_id" not in cols:
        db.execute("ALTER TABLE events ADD COLUMN headliner_id TEXT")
    db.commit()


def wiki_summary(wiki_url, retries=1):
    """Fetch a plain-text summary from Wikipedia's REST API for a wiki page URL.

    Returns (bio_or_None, ok). ok=False means the fetch itself failed (rate
    limit, timeout, server error) and should be retried on a later sync --
    as opposed to a confirmed 404 or disambiguation page, which is a real
    "this act has no usable bio" that's safe to cache forever.
    """
    title = urllib.parse.unquote(wiki_url.rstrip("/").rsplit("/wiki/", 1)[-1])
    url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title)
    # Wikimedia blocks/throttles requests with no identifying User-Agent.
    req = urllib.request.Request(
        url, headers={"User-Agent": "dmv-concert-aggregator/1.0 (personal, non-commercial project)"}
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, True
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"  [wiki-retry] {url} -- HTTP {e.code}, will retry next sync")
            return None, False
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"  [wiki-retry] {url} -- {e}, will retry next sync")
            return None, False
    if data.get("type") == "disambiguation":
        return None, True
    return data.get("extract"), True


def fetch_bios(attraction_ids):
    """Yield (id, name, wiki_url, bio, ok) for each attraction id, fetching fresh.

    ok=False means don't cache this result -- the fetch failed transiently
    and should be retried next sync, not treated as "no bio".
    """
    for aid in attraction_ids:
        full = get(f"attractions/{aid}")
        name = full.get("name")
        wiki_links = (full.get("externalLinks") or {}).get("wiki") or []
        wiki_url = wiki_links[0]["url"] if wiki_links else None
        bio, ok = wiki_summary(wiki_url) if wiki_url else (None, True)
        yield aid, name, wiki_url, bio, ok
        time.sleep(0.25)


def cmd_sync():
    missing = [v["key"] for v in VENUES if not v["id"]]
    if missing:
        sys.exit(f"No venue ID set for: {', '.join(missing)}. Run `venues` first.")

    now = datetime.now(timezone.utc).isoformat()
    db = sqlite3.connect(DB_PATH)
    ensure_schema(db)

    total = 0
    for v in VENUES:
        print(f"\n=== {v['name']} ===")
        for ev in fetch_events(v["id"]):
            row = normalize(ev, v["key"])
            if not row:
                continue
            cols = list(row) + ["first_seen", "last_seen"]
            vals = list(row.values()) + [now, now]
            # first_seen is preserved on conflict so you keep announcement history.
            updates = ", ".join(f"{c}=excluded.{c}" for c in row if c != "source_id")
            db.execute(
                f"INSERT INTO events ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(vals))}) "
                f"ON CONFLICT(source_id) DO UPDATE SET {updates}, last_seen=excluded.last_seen",
                vals,
            )
            total += 1
        db.commit()

    print(f"\nUpserted {total} events into {DB_PATH}")

    known = {r[0] for r in db.execute("SELECT attraction_id FROM attractions")}
    new_ids = {
        r[0] for r in db.execute(
            "SELECT DISTINCT headliner_id FROM events WHERE headliner_id IS NOT NULL"
        )
    } - known

    if new_ids:
        print(f"\nFetching bios for {len(new_ids)} new act(s)...")
        found = skipped = 0
        for aid, name, wiki_url, bio, ok in fetch_bios(sorted(new_ids)):
            if not ok:
                skipped += 1
                continue
            db.execute(
                "INSERT INTO attractions (attraction_id, name, wiki_url, bio, checked_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(attraction_id) DO UPDATE SET "
                "name=excluded.name, wiki_url=excluded.wiki_url, bio=excluded.bio, checked_at=excluded.checked_at",
                (aid, name, wiki_url, bio, now),
            )
            if bio:
                found += 1
        db.commit()
        note = f" ({skipped} will retry next sync)" if skipped else ""
        print(f"  {found}/{len(new_ids)} had a usable Wikipedia summary{note}")

    db.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "venues":
        cmd_venues()
    elif cmd == "sync":
        cmd_sync()
    else:
        sys.exit(__doc__)
