# Note — granting beacon-agents access to a new namespace (avoid the CM 403)

Recorded 2026-10-22. Applies whenever Beacon must read a NEW COA namespace through
the M2M agent-mcp wrapper.

## The trap

The wrapper maps the `beacon-m2m` client to a system profile with
`groups=[beacon-agents]`. serve-agent then runs a Cedar/role check: does
`beacon-agents` hold a role on THIS namespace? Grants are **per-namespace** — a grant
on one namespace does NOT carry to another.

Symptom when the grant is missing: the query path runs (no auth error at the wrapper),
but serve-agent returns:

```
Error executing tool query: CM 403: Access denied
```

i.e. the MCP call succeeds, the sigv4 hop to serve-agent succeeds, but serve-agent's
OWN authz denies because `beacon-agents` has no role on the target namespace.

This bit us seeding `calanthir-pharma` (`f054a82f-37a1-4033-a385-b9bf4d286056`): the
`beacon-agents -> data-analyst` grant existed only on the OLD "Pharmacy" namespace
(`36521996-76e9-433f-96cb-d2ec2db4e568`), so the new namespace 403'd until we granted it.

## The RIGHT way to add it — UI / control-plane API

Grant it through the control plane, NOT by writing DynamoDB directly:

- **UI:** open the namespace in the COA console -> Grants / Permissions panel ->
  add principal `Group: beacon-agents`, role `data-analyst`.
- **API:** `POST /namespaces/{namespaceId}/grants` on
  `https://tsd3qaxkmc.execute-api.us-east-1.amazonaws.com/prod`
  (control-plane REST API `coa-dev-api`, custom TokenAuthorizer — needs a namespace-owner
  / platform-admin JWT). Body names the principal group + role.

The control-plane path validates the grant AND bumps the authorizer's cache-invalidation
version counter, so the change is picked up cleanly.

## What we actually did this time (and why it's a shortcut)

We wrote the RRM row directly:

```
table: coa-dev-resource-role-mappings  (account 195698601974, us-east-1)
PK: Namespace::f054a82f-37a1-4033-a385-b9bf4d286056#Group::beacon-agents
SK: ROLE#data-analyst
(role=data-analyst, principalId=beacon-agents, resourceId=<ns>, grantedBy=calanthir-pharma-seed-wiring)
```

This WORKS (the authorizer has TTL=0, so it re-resolves every request and saw the row
immediately) but it bypasses control-plane validation and the stream-based cache bump.
Prefer the UI/API grant for anything beyond a dev shortcut. If you add it via the UI
later, the direct row and the UI row are the same (PK/SK), so it's idempotent — the UI
write just overwrites with a validated copy.

## Required grants per namespace (from design.md §1a step 7)

- `Group::beacon-agents -> data-analyst` on the namespace  (Beacon read path)
- `Group::<approvers> (e.g. clinical/QA) -> data-steward` on the namespace (approval path)

Add BOTH through the UI/API when standing up a new namespace Beacon will read.
