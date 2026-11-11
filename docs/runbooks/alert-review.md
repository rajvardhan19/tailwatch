# Alert review and outcomes

C25–C26 add tenant-scoped canonical pair episodes, append-only draft/review/approved/vetoed/superseded events, separate outcome events, hash verification and checkpoint export. Reversed directions within 180 days reuse the original episode and preserve its direction. Publication and delivery must require the latest approved state; superseding appends history.

Use caller-owned PostgreSQL transactions. Episode row locks serialize event appends; advisory locks serialize pair creation. Database triggers reject event/episode updates, deletes and truncation. Hashes verify ordered event content; export the head/sequence checkpoint through a separately administered object store to detect later privileged rewriting. The application cannot guarantee independence if the same administrator controls database and checkpoint storage. No-deal outcomes require mature 180-day followup and affirmative label coverage; announcement outcomes remain separate from withdrawal and returns.

Drafts are schema-bound evidence quotations. Every quote must occur in retained source text, and external display requires a permitted evidence ID. Optional model revision/prompt hash/cost are logged; there is no text-driven tool execution and no model approval path. Instructions embedded in source text stay quoted data. The default uses deterministic drafting. Unsupported claims, missing evidence and extraction timeouts cannot approve an alert. Analyst authorization, final human review and live release gates apply before customer exposure/delivery. No LLM provider is enabled and no hidden paid calls occur.

New alert hashes use a `v2:` prefix and bind the database event timestamp plus tenant, directional pair, model, feature-run identity, synthetic flag and episode creation time. Verification still accepts legacy hashes, whose weaker historical coverage is not retroactively rewritten. Appending verifies the existing chain first. `verify_checkpoint` compares an independently retained checkpoint against the current history prefix, accepting legitimate new events but rejecting missing/changed retained history. Independent storage/control remains an operational requirement, not something a local directory proves. Synthetic and real episodes now have separate cooldown lookups so a demo cannot suppress the first real episode for 180 days.

## Scoring and drafts

`tailwatch fit-candidate INPUT.json --objects DIR [--feature NAME ...]` fits a calibrated L2 logistic candidate from a validated research input. It retains the canonical artifact, whose SHA-256 is the model hash used by sealed evaluation and release manifests, and never promotes it. `tailwatch draft-alerts --run RUN.json --model-hash HASH --objects DIR --tenant ID --threshold P --budget N --actor NAME` then does the following:

- Verifies the feature-run and model commitments. Synthetic and real inputs cannot be mixed, the candidate must be calibrated, and it must have been fitted at or before the run cutoff, so no hindsight model scores an earlier run.
- Scores every frozen direction. Missing or unavailable required inputs are recorded as unscorable, with reasons, never as zero. Signed contributions plus the reference logit reconstruct the raw logit.
- Keeps directions at or above the calibrated threshold, highest first, one direction per company pair, up to the alert budget.
- Retains the complete scoring record and appends `draft` events only. An existing episode inside the 180-day window is reported, not redrafted.

Draft public content shows only evidence IDs passed with `--public-evidence` after rights review (synthetic fixture references are exempt) and omits probabilities for synthetic runs. It also records `conditions`: every frozen signal's availability state, value and contribution, so unavailable inputs stay visible rather than becoming zero. `visits` holds locations from `--visits` only for displayable, completed, pre-cutoff visits. Real candidates without an active release can be drafted and reviewed in shadow mode, but customer routes and delivery still require the active release's exact model.

## Independent checkpoints

`tailwatch checkpoint-alerts --objects INDEPENDENT_DIR` verifies every chain, retains its head and writes a content-addressed manifest; broken chains are reported and the command exits 1. `tailwatch verify-checkpoints MANIFEST --objects INDEPENDENT_DIR` confirms each retained head is still a prefix of current history. Store the directory and the printed manifest digest under separate administrative control from the database.
