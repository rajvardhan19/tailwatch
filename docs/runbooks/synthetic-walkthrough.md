# Synthetic operator walkthrough

This exercises the full workflow on synthetic data: frozen features → retained candidate → review-only drafts → human approval → verified delivery → audit and monitoring. It needs no provider credentials and sends no external email. `tests/integration/test_synthetic_walkthrough.py` runs the same sequence in CI.

Nothing here is evidence for a release gate. Synthetic alerts never publish a probability, and external sending stays disabled unless a validated release is active.

## Setup

```sh
docker compose up -d --wait
export TAILWATCH_DATABASE_URL=postgresql+psycopg://tailwatch:tailwatch@localhost:5432/tailwatch_test
export TAILWATCH_LINK_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export TAILWATCH_PUBLIC_URL=http://127.0.0.1:8765
uv run --locked alembic upgrade head
uv run --locked uvicorn tailwatch.product.server:app --host 127.0.0.1 --port 8765
```

Run the server and the commands below in shells with the same environment. The CLI does not load `.env`. The link secret signs recipient links. Changing it invalidates links already sent and prevents idempotent retries of their messages. `.data/` is git-ignored.

## 1. Workspace and recipient verification

```sh
uv run --locked tailwatch provision-workspace --name "Synthetic desk" \
  --email reader@example.invalid --output .data/workspace.json
uv run --locked tailwatch issue-key --tenant TENANT_ID --scope analyst --output .data/analyst.json
uv run --locked tailwatch send-verifications --mail-dir .data/mail
```

The operator file holds only the customer key. The verification link goes to the recipient's mailbox: locally, `.data/mail/MESSAGE_ID.txt`. Open its `Confirm` link and press the button. The token travels in the URL fragment and is posted only on that click. Links expire after 24 hours and work once. Use `request-verification SUBSCRIPTION_ID --actor NAME` for a new message (at most three per day).

## 2. Candidate model and drafts

```sh
uv run --locked tailwatch fit-candidate config/research.synthetic.json --objects .data/objects --feature flight
uv run --locked tailwatch research-demo > .data/run.json
uv run --locked tailwatch draft-alerts --run .data/run.json --model-hash MODEL_HASH \
  --objects .data/objects --tenant TENANT_ID --threshold 0 --budget 1 --actor you
```

`fit-candidate` retains a canonical calibrated artifact and prints `promotion_allowed: false`. `draft-alerts` verifies the run and model commitments and scores every frozen direction. Missing inputs are counted as unscorable, never as zero. It retains the full scoring record and appends `draft` events only. Re-running inside the 180-day episode window reports `existing_episodes` instead of redrafting.

## 3. Human review

```sh
ANALYST="$(python3 -c 'import json; print(json.load(open(".data/analyst.json"))["key"])')"
curl -s -H "Authorization: Bearer $ANALYST" http://127.0.0.1:8765/analyst/alerts
for kind in review approved; do
  curl -s -X POST -H "Authorization: Bearer $ANALYST" -H 'Content-Type: application/json' \
    -d "{\"kind\":\"$kind\",\"note\":\"Checked synthetic evidence\",\"human_confirmed\":true}" \
    http://127.0.0.1:8765/analyst/alerts/EPISODE_ID/review
done
```

Analysts see internal score details and the hash-verified history at `/analyst/alerts/EPISODE_ID`. Customers see only approved public content. Record outcomes with `POST /analyst/alerts/EPISODE_ID/outcome`. Non-pending outcomes need evidence, and `no_deal` needs mature 180-day follow-up with complete coverage.

## 4. Delivery

```sh
uv run --locked tailwatch outbox-enqueue --tenant TENANT_ID --actor you
uv run --locked tailwatch outbox-deliver --mail-dir .data/mail
```

Enqueue picks up current approvals for verified, active destinations that subscribed before the approval. It is idempotent. Delivery commits a `sending` state and the exact payload digest before calling the sender, then rechecks approval, subscription and release gates. Each message carries a signed unsubscribe link.

## 5. Monitoring and audit

```sh
uv run --locked tailwatch ops-status
uv run --locked tailwatch checkpoint-alerts --objects /independent/storage
uv run --locked tailwatch verify-checkpoints MANIFEST_DIGEST --objects /independent/storage
uv run --locked tailwatch outbox-reconcile --stale-minutes 30 --retry --mail-dir .data/mail
uv run --locked tailwatch revoke-key KEY_HASH
```

`ops-status` prints a JSON report and exits 1 when a declared threshold is breached, so a scheduler can alert on it. Keep checkpoint manifests in storage the database administrator cannot rewrite, and record each manifest digest outside the database. See [API delivery](api-delivery.md) for uncertain-delivery handling.
