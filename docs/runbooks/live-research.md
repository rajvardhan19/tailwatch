# Live research feeds

The research terminal can stream two real data feeds. Both run in **research mode**: they never produce signals, scores, alerts or emails, and nothing in them is a prediction. Reviewed signals still come only from the gated pipeline (drafts, human approval, and for real data an active validated release).

| Feed | Source | What the terminal shows |
| --- | --- | --- |
| Air traffic | [ADSB.lol](https://api.adsb.lol/api/openapi.json) live ADS-B, ODbL 1.0 | Anonymous en-route positions around 14 regional hubs |
| Insider filings | SEC EDGAR latest Form 4 feed, public data | Issuer, reporting owners and transaction codes as filed, linked to the filing |

## Start it

You need PostgreSQL migrated to `0010`, a source manifest, and an SEC User-Agent that declares an organisation and contact email.

```sh
export TAILWATCH_DATABASE_URL=postgresql+psycopg://…        # migrated to 0010
export TAILWATCH_SEC_USER_AGENT="Your Org contact@your.domain"
export TAILWATCH_LIVE_MANIFEST=config/sources.live.json
export TAILWATCH_LIVE_OBJECTS=.data/sec-objects             # journaled SEC responses
uv run --locked alembic upgrade head
uv run --locked uvicorn tailwatch.product.server:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765. On a live server the page never falls back to synthetic data; it asks for an API key. Provision one with `tailwatch provision-workspace` (customer key) or `tailwatch issue-key` (analyst key). `/live/status` is public, but `/live/traffic` and `/live/filings` require a key, so the data is not served to anonymous visitors.

## Rights and budgets

`config/sources.live.json` records the maintainer's research-use review for both sources, valid until 2027-02-11, research mode only, with external evidence display and B2B distribution not permitted. Every request is authorized against that record and reserved in a durable daily budget first: 6,000 ADSB.lol requests and 3,000 SEC requests per UTC day. An exhausted budget or expired permission pauses the feed for 15 minutes; it never retries hot. Provider errors back off exponentially, up to 10 minutes.

ADSB.lol publishes no numeric rate limit and asks to be contacted before **production** use. The terminal polls one hub every 6 seconds, which is about 10 requests a minute, and only while someone has viewed the live endpoints in the last two minutes. SEC polling is one feed request a minute, plus at most four filing-detail fetches of two requests each. It shares `capture-sec`'s single-worker advisory lock and stays far below EDGAR's 10 requests/second fair-access limit. SEC responses are journaled in `sec_fetches` and the object store. Air-traffic responses are display-only and are not retained.

To turn a feed off, set `"enabled": false` for that source, or unset `TAILWATCH_LIVE_MANIFEST` and restart.

## Privacy and limits

- **No identity leaves the server.** Hex, callsign, registration and aircraft type are dropped inside `tailwatch.sources.airtraffic`. Positions are coarsened to 0.05°, and output order is sorted so it carries no identity.
- **Excluded aircraft:** those flagged military, PIA or LADD in readsb `dbFlags` (bits 1, 4 and 8). Flags exist only for aircraft in the provider database, so traffic on the ground or below 5,000 ft is also excluded. That hides the take-off and landing phases that would reveal which airport an aircraft uses.
- **Coverage is partial.** It is 250 nm circles around the listed hubs, which is neither global nor complete. Stale hubs (over 5 minutes) are dropped rather than shown as current.
- **Positions are not completed visits.** They do not feed features, aircraft-control links or alerts. Between polls the terminal dead-reckons each dot from its reported track and speed, for at most two minutes.
- **Form 4 codes are as filed.** Code P is not a reviewed discretionary open-market purchase. Classification still requires `TradeReview` and the amendment rules.

Turning these feeds into signals still needs G0–G5: reviewed coverage, an aircraft-control review, real labels, a sealed-test PASS, shadow sessions and a release.
