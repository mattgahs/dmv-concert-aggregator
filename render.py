#!/usr/bin/env python3
"""
Render events.db -> index.html

Usage:
    python render.py                 # writes index.html next to template.html
    python render.py --out docs/     # for GitHub Pages serving from /docs

Events are inlined into the HTML rather than fetched as a separate JSON file,
so the page works over file:// and there is one artifact to publish.
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone

FIELDS = [
    "e.source_id", "e.venue", "e.title", "e.headliner", "e.support",
    "e.start_utc", "e.local_date", "e.local_time", "e.ticket_url",
    "e.on_sale_utc", "e.status", "e.first_seen", "e.segment",
]


def load_favorites(path):
    if not os.path.exists(path):
        return {"acts": [], "shows": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {"acts": data.get("acts", []), "shows": data.get("shows", [])}


def load(db_path):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    today = datetime.now(timezone.utc).date().isoformat()

    # NULL local_date sorts last so TBA-date shows land at the bottom.
    # attractions is a bio cache keyed by Ticketmaster attraction id (not
    # name), so this join is exact even when two different acts share a name.
    rows = db.execute(
        f"SELECT {','.join(FIELDS)}, a.bio, a.wiki_url FROM events e "
        "LEFT JOIN attractions a ON a.attraction_id = e.headliner_id "
        "WHERE e.local_date IS NULL OR e.local_date >= ? "
        "ORDER BY e.local_date IS NULL, e.local_date, e.local_time",
        (today,),
    ).fetchall()
    # Every sync stamps first_seen/last_seen with one shared timestamp, so the
    # newest last_seen identifies the latest run and first_seen == that value
    # means "first appeared in that run".
    last_sync = db.execute("SELECT MAX(last_seen) FROM events").fetchone()[0]
    db.close()
    return [dict(r) for r in rows], last_sync


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.environ.get("IMP_DB", "events.db"))
    p.add_argument("--template", default="template.html")
    p.add_argument("--out", default=".")
    p.add_argument("--favorites", default="favorites.json")
    args = p.parse_args()

    events, last_sync = load(args.db)

    favorites = load_favorites(args.favorites)
    fav_shows = set(favorites["shows"])
    fav_acts = {a.lower() for a in favorites["acts"]}
    for ev in events:
        ev["fav_show"] = ev["source_id"] in fav_shows
        ev["fav_act"] = bool(ev["headliner"]) and ev["headliner"].lower() in fav_acts

    with open(args.template, encoding="utf-8") as f:
        html = f.read()

    payload = {
        "built": datetime.now(timezone.utc).isoformat(),
        "last_sync": last_sync,
        "favorite_acts": favorites["acts"],
        "events": events,
    }
    # A band name containing "</script>" would close the tag early and break
    # the page, so neutralise any closing-tag sequence.
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    open_tag = '<script id="data" type="application/json">'
    start = html.index(open_tag) + len(open_tag)
    end = html.index("</script>", start)
    html = html[:start] + "\n" + blob + "\n" + html[end:]

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Wrote {out_path} ({len(events)} events, {len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
