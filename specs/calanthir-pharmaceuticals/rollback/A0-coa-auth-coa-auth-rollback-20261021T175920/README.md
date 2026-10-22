# A0 — COA Auth Rollback Snapshot (2026-10-21T17:59:20Z)

Durable rollback point captured **before** any Loom repoint (Task Set A). Taken with
`AWS_PROFILE=agentops-publisher` (account 195698601974, us-east-1). This folder lives in
the repo (durable); the original was under `$KIROCREW_SCRATCH` (reclaimed at session end).

## Current state (the values to restore to)

**Trust anchor today:** `coa-dev-user-pool` = `us-east-1_hSuaktFXR`
**Clients:** web app `7ru7vrf5a9m1dvpgq2eocg9esp` · MCP/CLI `3p4ganoan7328djo9e9727939r`
**Issuer:** `https://cognito-idp.us-east-1.amazonaws.com/us-east-1_hSuaktFXR`

### The 5 auth bindings (all point at the COA pool now)
1. **Web app OIDC** (`webapp-runtime-config.json`): `authority` = COA issuer,
   `clientId` = `7ru7vrf5a9m1dvpgq2eocg9esp`. Served at
   `https://d3upg0x9odcmsc.cloudfront.net/runtime-config.json`.
2. **Serve runtime** `coa_dev_context_manager-8Q2n5Z9YSY` (`serve-runtime.json`):
   `customJWTAuthorizer.discoveryUrl` = COA issuer,
   `allowedAudience` = [`7ru7vrf5…`, `3p4ganoan…`].
3. **MCP runtime** `coa_dev_mcp_server-MkoY1fCoME` (`mcp-runtime.json`): identical
   authorizer (COA issuer + same `allowedAudience`). Also env
   `JWT_ISSUER_URL`/`JWT_CLIENT_ID` on the container (second in-container check).
4. **SSM `/coa/*`** (`ssm-coa.json`): `/coa/issuer`, `/coa/userpool-id`,
   `/coa/userpool-client-id`, `/coa/cognito-domain-prefix` all = COA pool values.
5. **Control-plane API authorizer** (`controlplane-authorizers.json`): a **custom Lambda
   authorizer** `TokenAuthorizer` (id `ndiz9o`) -> Lambda `coa-dev-authorizer`. NOTE:
   repointing (A7) means changing **that Lambda's issuer/pool config**, NOT a native
   Cognito authorizer on API Gateway.

### Roles / groups baseline
- `coa-dev-roles.json` — 7 built-in roles (Default, Platform Admin/Viewer, Namespace
  Owner/Maintainer, Data Steward, Data Analyst).
- `coa-groups.json` — one group: `Admin` -> `platform-admin` (pre-seeded).

### Users stranded by the repoint (accepted)
- `nobody@amazon.com` (943884f8-…) — original seeded placeholder.
- `subhaviv@amazon.com` (c4681438-…) — login created this session (pw `Coa2026$demo`).
Both live in `coa-dev-user-pool`; after repoint to Loom they no longer authenticate to
COA. Acceptable per plan (COA pool retired eventually).

## How to roll back (restore COA to trust its own pool)
1. **Serve/MCP runtimes:** `update-agent-runtime` (or re-`cdk deploy` original COA source)
   setting `authorizerConfiguration` back to `serve-runtime.json` / `mcp-runtime.json`
   (COA issuer + `allowedAudience` as captured); restore container
   `JWT_ISSUER_URL`/`JWT_CLIENT_ID`.
2. **SSM `/coa/*`:** `put-parameter --overwrite` each name to the value in
   `ssm-coa.json`; redeploy serve + MCP containers to re-read.
3. **Control-plane:** restore the `coa-dev-authorizer` Lambda's issuer/pool config to the
   COA pool.
4. **Web app:** restore `runtime-config.json` `authority`/`clientId` to the COA values
   (`webapp-runtime-config.json`) and redeploy the SPA config.
5. **Grants/groups:** COA `Admin`->`platform-admin` seed is unchanged; delete any Loom
   group->role grants added during A8 to return to baseline.

**Best practice:** make the repoint in COA's **CDK source** (not raw CLI) so restore is a
git revert + `cdk deploy`. The CLI snapshots here are the authoritative values either way.

## Files in this folder
`serve-runtime.json`, `mcp-runtime.json`, `ssm-coa.json`, `coa-pool.json`,
`coa-client-webapp.json`, `coa-client-mcp.json`, `coa-groups.json`, `coa-users.json`,
`coa-dev-roles.json`, `controlplane-authorizers.json`, `controlplane-restapi.json`,
`webapp-runtime-config.json`.
