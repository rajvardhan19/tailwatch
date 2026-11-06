# Free-first defaults (researched November 6, 2026)

The user requested a free-first recommendation rather than selecting vendors. These are development defaults, not validated production readiness.

- Flight research: [ADSB.lol](https://api.adsb.lol/docs), free ODbL data. Production use requires contacting the maintainer per its guidance; review attribution/share-alike implications before distributing derived databases. The implemented bounded adapter captures states only for an explicit reviewed aircraft allowlist. Raw positions are NOT completed flights or proof of parking. No privacy exclusions are bypassed. Historical trace-to-leg validation remains necessary before live features.
- [OpenSky terms](https://opensky-network.org/about/terms-of-use) require written licensing for commercial and operational use; it is not an unrestricted free production alternative.
- Local Docker/PostgreSQL is the default zero-subscription development stack. Optional demo: [Render free web service](https://render.com/docs/free) plus [Neon free PostgreSQL](https://neon.com/pricing). Render sleeps after inactivity and free Render Postgres expires after 30 days; never use that expiring database as the evidence archive. Neon quotas are limited; bulk aircraft traces will require explicit storage budgeting. No account has been provisioned.
- Email: [Resend](https://resend.com/pricing), free tier has a 100-message daily limit at review time. A verified sender/domain, credentials and verified opted-in recipients are required. Development uses a stub and sends nothing.
- Sector: start the feasibility inventory with US-listed industrial operating companies as a research hypothesis, not a claim of sufficient coverage. Freeze the final eligible universe only after registry, disclosures, and history overlap are measured. Avoid inventing data, targets or model accuracy to satisfy a launch gate.

Recheck quotas and terms before deployment. Zero subscription cost does not imply zero operational/storage cost or guaranteed historical coverage. Production remains gated by G0–G5.
