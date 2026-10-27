# Manufacturing news feed

A live, paged feed of **manufacturing-only** news, organised the way `memory/` is: one stream per taxonomy category, per country and per
manufacturing hub. UI: *Manufacturing News*. API: `GET /api/feed`, `GET /api/feed/facets`.

## Collectors (all keyless, headline + link + date only)

| Source | What it gives | Pace |
|---|---|---|
| GDELT DOC API | one query per taxonomy category (from `emithran/news/topics.yaml`) and one per country (`sourcecountry:`), English sources | every 15 min, 10 s between requests (GDELT asks for 1 per 5 s) |
| Hacker News (Algolia) | discussions matching each category's keywords, last 90 days, with points and comment counts | every 15 min |
| Supply Chain Dive, Manufacturing Dive | trade-press RSS | every 15 min |
| NIST manufacturing news | government RSS | hourly |

The registry refuses polling faster than every 15 minutes (a politeness guard); GDELT itself updates every 15 minutes.

## What "only manufacturing" means

Every headline, whatever its source, goes through `emithran/news/classify.py` before it is stored. It is rules, not a model, so the same headline always
gets the same tags and each tag can be traced to the words that caused it:

* `cat:<slug>` a taxonomy category (e.g. `die-casting`); ambiguous words ("casting", "forging", "assembly") only count with manufacturing context.
* `country:<ISO3>` named in the headline or implied by a hub (Pune implies India); `loc:<place>` the hub; `pub:<ISO3>` the publisher's country.
* A headline with no category and no manufacturing vocabulary is **rejected** (counted as `items_filtered`, never stored). Feeds that are
  manufacturing publications are trusted, as are trade-media domains.

Edit `topics.yaml` to change categories, keywords, countries and hubs; collectors read it when they plan a run. Re-tag stored headlines with
`python -m emithran.cli tag-news`.

## Live behaviour and history

* The server announces new documents over `/api/stream`; the open feed fetches only what is newer than its top item and prepends it with a short highlight.
  If you are scrolled down, paused, or on another page, a "N new headlines" button appears instead; nothing jumps under you.
* Nothing is overwritten: older headlines stay in pages (25/50/100 per page), filterable by category, country, location, source and text.
* Items dated in the future (event listings) sort by when they were first seen.

## Agents

The News & Supply-Chain Risk agent reads the feed: risk themes, **country hotspots** and **category hotspots** (with the headlines as evidence), and
tags its flags `country:` / `cat:` so they corroborate FX and labour flags for the same country. Every flag change (new, worse, better, resolved,
back) is kept in the Activity history on the Agent Intelligence page.

## Known limits

* GDELT throttles aggressively and shares limits by IP. After a 429 without `Retry-After` the fetcher now waits 30 s, 60 s, ... and pauses the whole
  host; a throttled run is slow, not broken. English-language sources only.
* No Reddit: it is API-only with your credentials and commercial-use terms; not built.
* Headlines are classified from the title only (GDELT gives no summary), so recall is modest by design: precision comes first.
