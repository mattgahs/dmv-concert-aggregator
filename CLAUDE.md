# DMV concert aggregator

A personal dashboard of upcoming shows at 11 venues around DC, Baltimore and
northern Virginia. Runs as a daily cron, publishes a static page.

## Architecture

Deliberately boring. No server, no hosted database.

1. GitHub Actions cron (daily) runs `imp_fetch.py sync`
2. That hits the Ticketmaster Discovery API and upserts into `events.db` (SQLite)
3. `render.py` queries the DB and writes `index.html` from `template.html`
4. The Action commits `events.db` and `index.html`; GitHub Pages serves the HTML

The DB is a build-time artifact. It is never served to the browser — events are
inlined into `index.html` as a JSON data island. The repo is the persistence
layer; `events.db` survives between runs only because git holds it.

## Files

- `imp_fetch.py` — Ticketmaster adapter. `venues` subcommand resolves venue IDs,
  `sync` fetches events. Stdlib only.
- `render.py` — reads `events.db` and `favorites.json`, writes `index.html`.
- `template.html` — the dashboard. Self-contained, opens standalone with sample
  data. Vanilla JS, no build step, no framework.
- `favorites.json` — hand-committed list of favorited acts and shows (see
  Favorites below). Starts empty; edited via the export flow in the page, not
  by hand.

## Favorites

Two independent things can be favorited: a specific show (star icon — "I want
to go to this one") and an act by name (follow pill next to the headliner —
"flag this act wherever/whenever they play"). Both filter into the "★
Favorites only" chip. An act can also be followed with zero current shows —
the "Manage favorites" panel at the bottom has a plain text-input for this —
so it's a real watchlist: follow a band with nothing scheduled and it flags
itself the moment a matching show gets synced in. That panel also lists every
followed act (noting which have no upcoming show) with an unfollow control.

The durable copy is `favorites.json` at the repo root
(`{"acts": [...], "shows": [...]}`, acts matched case-insensitively against
`headliner`, shows matched by `source_id`). `render.py` reads it and bakes
`fav_show`/`fav_act` booleans onto every event plus the raw `favorite_acts`
list into the page's data island — that's the only way favorites reach the
page; there's no client-side matching against the committed list.

Clicking a star/follow in the browser writes to `localStorage` immediately
(key `dmv_favorites_overrides_v1`) — that's what makes clicking responsive
without a server. Since GitHub Pages can't write back to the repo, that's also
where it stays until you act: open the "export" panel at the bottom of the
page, download the resulting `favorites.json`, and commit it over the
existing one. On the next render, baked-in values catch up to what you
exported, and the matching `localStorage` overrides — now redundant — quietly
prune themselves on next page load. Until you export and commit, favorites
exist only in the browser you clicked them in.

## Event types

`sync` keeps every Ticketmaster event and stores its Discovery segment (Music,
Arts & Theatre, Sports, Film, Miscellaneous) in `events.segment`; the page has
type chips for filtering (all shown by default). It used to keep Music only.
The one remaining exclusion is hospitality add-ons that Ticketmaster lists as
pseudo-events next to the real show — CFG Bank Arena's "Fearless Club" and
"NINETEEN 62" packages and "(UPSELL ONLY)" VIP items (`PACKAGE_RE`) — logged as
`[reject]` so the rule can be sanity-checked. The first sync after the segment
column is added backfills existing rows as Music and backdates `first_seen` so
the newly visible non-music backlog isn't all flagged "new".

## Artist bios

Each show can carry a short "About {act}" disclosure, sourced from Wikipedia
via the exact `externalLinks.wiki` URL on the act's Ticketmaster attraction
record (not name-matching — avoids collisions between differently-named
acts). Fetched once per act during `sync` and cached forever in `events.db`'s
`attractions` table; acts with no Wikipedia link just get no disclosure, no
retry. This is *not* the press-kit bio text Ticketmaster's own site sometimes
shows next to an event — that text isn't available through the public
Discovery API from any endpoint we could find; Wikipedia's summary API is the
closest free, keyless substitute, so the tone reads more encyclopedic than
promotional.

Note: `imp_fetch.py` is misnamed — it started as I.M.P. venues only and now
covers most of the DMV. Worth renaming to `fetch_tm.py`.

## Venues

DC (I.M.P.): 9:30 Club, The Atlantis, Lincoln Theatre, The Anthem
Silver Spring: The Fillmore
Columbia: Merriweather Post Pavilion
Baltimore: Soundstage, Nevermore Hall, Pier Six Pavilion, CFG Bank Arena
Vienna VA: Wolf Trap

In the UI, colour encodes city rather than venue — 11 distinguishable hues is
not achievable, and city is the more useful cut anyway.

## Ticketmaster Discovery API notes

- Free key from developer.ticketmaster.com. Limits: 5000 calls/day, 5/sec.
- **Deep paging is capped at `size × page <= 1000`.** You can never retrieve
  past the 1000th result of a query. This is why we query per-venue rather than
  one market-wide query — a DMV-wide query would silently truncate. Do not
  "optimise" this into a single call.
- **Venue IDs**: the `id` we need looks like `KovZpZA6tnEA`. The numeric IDs in
  ticketmaster.com URLs (e.g. `/venue/172718`) are legacy IDs and the API
  rejects them.
- Per-venue volume is well under the cap. ~35 calls per daily run total.

## Open questions — status as of 2026-09-13

These were written against docs without a live API key. Checked via public
Ticketmaster/venue pages and API docs (no key needed for that); the one thing
that genuinely requires a live key — actual JSON response shape — is still
open. Re-verify item 1 against the first real `sync` output.

1. **Field paths — confirmed against a live response (2026-09-13).** First
   `sync` upserted 398 events across all 11 venues; only 4 have a null
   headliner, and each of those is a genuinely headliner-less theme/tribute
   night ("80s Prom Experience," "House Party" — not a parsing failure).
   `_embedded.attractions` is populated in practice, so the headliner/support
   split works as designed. One thing the filter caught correctly: CFG Bank
   Arena lists premium hospitality packages ("Fearless Club at CFG Bank
   Arena — Billy Strings," "The NINETEEN 62 at CFG Bank Arena — …") as
   separate `Miscellaneous`-segment pseudo-events alongside the real
   `Music`-segment listing for the same show. The `KEEP_SEGMENTS` filter
   drops the packages and keeps the real event — verified Smashing Pumpkins,
   Billy Strings, Dave Matthews Band, Doja Cat (w/ Latto), and Andrea Bocelli
   all made it through as proper Music-segment rows.
2. **Wolf Trap coverage — resolved, with a wrinkle.** "Wolf Trap" is two
   venues: the Filene Center (outdoor, May–Sep) and The Barns at Wolf Trap
   (indoor, Oct–May). The Filene Center is on Ticketmaster with real,
   non-resale inventory (verified against its live TM venue page, which lists
   actual September 2026 shows). The Barns does **not** appear on Ticketmaster
   at all — not even a stale listing; only resale sites (StubHub, SeatGeek,
   VividSeats) carry it, sourced elsewhere. Also: Ticketmaster has a second,
   stale "Wolf Trap" venue entry at the same address with zero events — when
   running `venues`, pick the one named "Filene Center", not plain "Wolf
   Trap". **Decision: Filene Center only, in scope.** The Barns is out of
   scope — it's a different program (classical/chamber/comedy vs. touring
   concerts) and would need the Tickets.com MPV adapter to cover at all. No
   second adapter needed for the current venue list.
3. **Pier Six — approach confirmed still correct.** Currently trading as
   plain "Pier Six Pavilion" (no active naming-rights sponsor as of
   2026-09-13), after a spell as "MECU Pavilion." Confirms the rename risk is
   real — keep matching on address (Pier Six, Inner Harbor), not name. Still
   seasonal; empty most of the year is expected.
4. **Fillmore Silver Spring — confirmed on Ticketmaster.** Listed as a Live
   Nation room ("presented by Cricket Wireless"), with live 2026 event dates.
   One thing to watch during `venues`: TM search surfaced two different venue
   IDs for it (one likely stale/duplicate, as with Wolf Trap) — confirm by
   address before pasting the ID in.

## Known first-run problem

`first_seen` is stamped at insert time, so the first sync marks every event as
seen today and the "added in the past 2 weeks" filter returns everything for a
fortnight. After the first successful sync, backdate them:

```sql
UPDATE events SET first_seen = '2020-01-01T00:00:00+00:00';
```

Then subsequent runs will flag genuinely new announcements.

## Task order

1. Get an API key, export as `TM_API_KEY`
2. `python imp_fetch.py venues` — confirm each match by street address, not
   name, then paste the Discovery IDs into the `VENUES` list
3. Do the Wolf Trap coverage check (question 2 above) — it decides whether this
   is a one-adapter or two-adapter project
4. First `sync`. Read the reject log: the Music-segment filter is there to drop
   conventions, WWE and auto shows at Merriweather/Anthem, and rejects are
   logged rather than dropped silently so the filter can be sanity-checked.
5. Backdate `first_seen`
6. `python render.py`, open `index.html`, check the headliner/support split
   actually populated
7. Repo, Actions workflow, enable Pages (Settings → Pages → source: GitHub
   Actions). Set `concurrency: group` in the workflow — two overlapping runs
   would each start from the committed DB and the second commit would clobber
   the first.

## Preferences

Ask rather than assume. Simplest solution that works; no flexibility that isn't
needed yet. Don't touch unrelated code, but do surface design smells as separate
issues. Flag uncertainty explicitly rather than guessing confidently — where a
small low-risk experiment would settle a question, run it and report the result.
