# Publishing the research terminal

Two routes are available: a quick link from a running Mac, or permanent free hosting. Both run the read-only public mode described in [live research feeds](live-research.md#start-it). Before announcing the site, send the courtesy notice to ADSB.lol (info@adsb.lol); it is listed in that runbook's attribution section.

## Quick link (Cloudflare quick tunnel)

```sh
brew install cloudflared
cloudflared tunnel --url http://127.0.0.1:8765
```

Run the server first, bound to `127.0.0.1` with `TAILWATCH_PUBLIC_VIEW=1`. The tunnel prints a random `https://….trycloudflare.com` address that anyone can open. It needs no account. The link works only while this machine, the server and the tunnel keep running, and it changes on every restart. Stop it with Ctrl-C, or by ending the process.

## Permanent free hosting (Render + Neon)

1. **Database.** Create a free project at https://neon.com, then copy its connection string. Use the **direct** host: turn off connection pooling in Neon's connect dialog, or delete `-pooler` from the host name. The SEC worker holds a session-level advisory lock, which a transaction-pooled connection cannot keep. `scripts/start.sh` switches `postgresql://` to the psycopg driver, and `?sslmode=require` is kept. PostGIS is available on Neon; migration `0009` enables it.
2. **Code.** Render deploys from GitHub. `render.yaml` points the service at `feat/live-research-feeds`; change its `branch` after merging into `main`.
3. **Web service.** At https://render.com, choose New → Blueprint and select this repository; `render.yaml` defines a free Docker web service. Fill in the secret variables when prompted:
   - `TAILWATCH_DATABASE_URL`: the Neon URL from step 1.
   - `TAILWATCH_SEC_USER_AGENT`: your name and contact email, as SEC fair access requires.
   - `TAILWATCH_PUBLIC_TENANT`: optional. It publishes one workspace's approved alerts. Create the workspace against the Neon database with `tailwatch provision-workspace` first.
4. **First deploy.** `scripts/start.sh` applies the migrations, then serves on Render's port. The service is named `tailwatch-live`, so the address is https://tailwatch-live.onrender.com (Render adds a suffix only if that name is taken).

Free-tier facts to keep in mind:
- **Sleep:** Render's free services sleep after inactivity and take roughly a minute to wake. The live feeds then warm up over about 70 seconds.
- **Shared ADSB.lol quota:** the 500 requests/day quota is counted per IP, and Render's outbound IPs are shared, so the air feed can pause earlier than it would from your own machine. The site shows the remaining quota.
- **No persistent disk:** journaled SEC responses (`TAILWATCH_LIVE_OBJECTS`) are lost on restart. Use a disk or object storage if they must be kept as evidence.

## Public security posture

- **What is public:** the research feeds, Past deals, the published workspace's approved alerts and track record, and the dashboard assets.
- **What still needs a key:** every write and every analyst route. The interactive API documentation is not served.
- **Watchlists:** saved only in each visitor's browser.
- **Company lookups:** limited to companies the site shows, queued onto the single SEC worker, and covered by the durable daily SEC budget.
- **What is not built in:** request rate limiting. Keep the site behind Cloudflare, or another proxy or CDN, before sharing it widely.
- **Never set** `TAILWATCH_ENABLE_PRODUCTION=1` or real email credentials on a public research deployment.
