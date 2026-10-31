# Model routes per plan: a note for the owner

[ADR 0011](../adr/0011-tiers-quotas-and-admin.md) section 6 and [ADR 0012](../adr/0012-eval-suite-and-model-providers.md)
section 5. Migration 0014 gave each plan a route key of its own: `free`, `instructor` and `pro`.

- A job takes the route key of its user's **effective plan** when it is created, so a route
  applies to the plan's next job only; a job already created keeps its key.
- Set or clear a route per plan key and stage group (`design`, `songs`) with the admin API
  (`PUT` / `DELETE /api/admin/models/routes/{route_key}/{stage_group}`, step-up, audited) or the
  models CLI (`python -m classbeat models enable|disable|list`). It is refused unless the model has a passing eval
  result on the running suite, and DeepSeek only while it is enabled.
- A plan with **no route** for a stage group uses the bootstrap choice (Anthropic with
  `CLASSBEAT_AGENT_MODEL` and `_EFFORT`), exactly as before M3c. Nothing fails.
- **An existing route on `default` no longer covers the paid plans** (or any seeded plan): after
  migration 0014 no job resolves the `default` key. If you set routes on `default` earlier, set
  them again on `free`, `instructor` and `pro`. Nothing breaks if you do not; those plans use the
  bootstrap choice.
- A route to a provider whose key file is missing on the worker fails its jobs as `model_config`
  at resolution, never silently. Provider key files live on the worker only.
