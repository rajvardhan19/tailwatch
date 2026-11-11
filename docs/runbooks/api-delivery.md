# API, keys, subscriptions and email delivery

C27–C28 use FastAPI and PostgreSQL. Construct `create_app(engine)`; production exposure is disabled by default. API keys are random 256-bit bearer tokens, stored only as SHA-256 digests, tenant-scoped, independently revocable and separated into customer/analyst scopes. `tailwatch issue-key --tenant ID --scope customer|analyst --output FILE` writes a new key once into an owner-only file and prints only its digest. `tailwatch revoke-key DIGEST` revokes it. To rotate, issue the new key, confirm the tenant and scope, then revoke the old digest. Never place keys in URLs or logs. Serve behind HTTPS and a reverse proxy that limits request rate and body size before public deployment.

## Customer and analyst routes

All customer views share one visibility rule: the episode is tenant-owned, its latest review state is approved, and it is either explicitly synthetic or produced by the active release's exact model. Drafts, vetoes and internal score details never reach customer routes.

| Route | Purpose |
| --- | --- |
| `GET /alerts`, `GET /alerts/{episode}` | Current approved alerts with latest outcome state; bounded UUID cursor pagination |
| `GET /alerts/{episode}/history` | Sanitized approved/superseded/outcome history |
| `GET /pairs/{visitor}/{host}` | Both orientations of a company pair; reverse-direction episodes are labelled, never re-attributed |
| `GET /companies/{company}/signals` | Alerts involving a company, with its role and the counterpart |
| `GET /companies/resolve?ticker=&exchange=&at=` | Issuer of the unique security using a ticker on that date, as known now. Ambiguity returns 409; future or naive dates 422; unconfigured `TAILWATCH_REFERENCE_SOURCE` 503 |
| `GET/PUT /watchlist`, `DELETE /watchlist/{company}` | Tenant watchlist by stable company UUID |
| `GET /track-record` | Original approved prediction plus latest append-only outcome |
| `GET /analyst/alerts?state=draft` | Analyst queue with internal score content (analyst scope only) |
| `GET /analyst/alerts/{episode}` | Full history with hash-chain verification status |
| `POST /analyst/alerts/{episode}/review` | Human review transition; approval copies only prepared public content |
| `POST /analyst/alerts/{episode}/outcome` | Evidence-backed outcome; `no_deal` needs mature, complete 180-day follow-up |

Resolve tickers before adding companies to a watchlist; alert routes accept stable UUIDs only.

## Recipient verification

`tailwatch provision-workspace --name NAME --email ADDRESS --output /private/path/new-credentials.json` creates a tenant, customer key, unverified subscription and a queued verification message. The output file is created exclusively with owner-only permissions; existing files and symlinks are never replaced. It contains the customer key and identifiers only, never recipient tokens, so an operator cannot confirm an address on the recipient's behalf.

`tailwatch send-verifications --mail-dir DIR` renders each queued message and writes it to a local mailbox. With `--external`, it sends through Resend instead (see below). Recipient links are `PUBLIC_URL/confirm#action=…&id=…&token=…`. The token is an HMAC of the purpose and message or delivery ID under `TAILWATCH_LINK_SECRET`. It travels in the URL fragment, so servers and proxies never log it. The `/confirm` page acts only when the recipient presses its button, so link scanners cannot verify or unsubscribe anyone. Verification links expire after 24 hours and are single-use. `request-verification` issues a new message, at most three per destination per day. Unsubscribe links in delivered alerts stay valid. Rotating the link secret invalidates every outstanding link.

The legacy `/subscriptions/{id}/verify` and `/subscriptions/{id}/unsubscribe` token routes remain for existing subscriptions. Authenticated customers can list their own subscriptions and disable one with `DELETE /subscriptions/{id}`. Neither listing nor errors return token hashes. There is no public signup route.

## Outbox

`tailwatch outbox-enqueue --tenant ID --actor NAME` queues each current approval for verified, active destinations created before that approval. It is idempotent: `(episode, approved sequence, subscription)` is unique. `tailwatch outbox-deliver --mail-dir DIR` claims one item at a time and commits `sending`, the first-attempt time and the exact payload digest before any external effect. It then locks the episode and subscription through the final state check and the provider call. Supersession, unsubscribe or a lost release cancels delivery. An unsubscribe that races a send already in progress takes effect after that attempt; sent mail cannot be recalled. Every transition is appended to the immutable `delivery_events` audit table.

Messages render deterministically from the immutable approved event, the delivery ID and the link signer. Synthetic alerts are labelled and never show a probability. Approved text is reduced to inert single-line strings.

| State | Meaning and operator action |
| --- | --- |
| `sent` | Provider acknowledged. Not proof of inbox delivery. |
| `cancelled` | Rechecked gates refused before any send. |
| `uncertain` | Timeout or missing acknowledgment; the message may have been sent. |
| `sending` older than the stale interval | Worker crashed mid-attempt; `outbox-reconcile --stale-minutes N` marks it `uncertain`. |
| deferred (stays `pending`) | Quota or release reservation refused; nothing left the process. Retry later. |

`outbox-reconcile --retry` resends uncertain alerts with the original idempotency key and a recomputed, byte-identical payload. It does this only when the sender supports idempotency and the first attempt is at least one hour inside the provider window. [Resend keeps idempotency keys for 24 hours](https://resend.com/docs/dashboard/emails/idempotency-keys) and returns the original email ID for an identical retry. A changed payload, for example after rotating the link secret or changing the public URL, is skipped and logged. Older items, and senders without idempotency, need `tailwatch outbox-resolve DELIVERY --state sent|cancelled --actor NAME --evidence TEXT [--provider-id ID]`, based on provider logs. Exactly-once email is never claimed. Uncertain verification messages are not retried; request a new verification instead.

## External sending

`--external` requires `TAILWATCH_ENABLE_PRODUCTION=1`, `TAILWATCH_RESEND_API_KEY`, `TAILWATCH_EMAIL_FROM` (a verified sender or domain) and the link settings. Each message independently rechecks that the episode is not synthetic, that the active validated release uses that exact model and includes the tenant, and that the release's daily email quota has room. Verification mail uses the same release, tenant allowlist and quota. Logs must never contain provider credentials, tokens or recipient payloads. CI uses reserved `.invalid` addresses, stub and file senders, and sends nothing.
