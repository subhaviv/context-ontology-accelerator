# Note — seed/source buckets MUST be named `coa-*` (serve-agent S3 read)

Recorded 2026-10-22. Applies to any S3 bucket you create to seed/catalog data
into a COA namespace (Glue Data Catalog source).

## The trap

COA executes a namespace query's SQL via Athena **as the serve-agent runtime role**
(`coa-dev-serve-agent-ServeAgentRuntimeExecutionRole...`). That role's S3 read grant is
scoped by a NAMING CONVENTION, not per-bucket:

```
s3:GetObject / s3:ListBucket  on:
  arn:aws:s3:::coa-dev-ontology-artifacts-195698601974 (+/*)
  arn:aws:s3:::coa-dev-athena-results-195698601974     (+/*)
  arn:aws:s3:::coa-*                                    (+/*)   <-- the wildcard
```

A seed bucket NOT named `coa-*` is unreadable by the serve role. The query then fails at
EXECUTION (not generation/auth) with:

```
Athena query FAILED: PERMISSION_DENIED: ... serve-agent role is not authorized to
perform s3:ListBucket on arn:aws:s3:::<your-bucket> because no identity-based policy
allows the s3:ListBucket action
```

and every query path (Playground, M2M wrapper, Beacon) returns empty / "no context",
because they all run SQL as that same role.

This bit us: the first seed bucket was `calanthir-pharma-seed-195698601974` — does NOT
match `coa-*`, so the serve role could not read it. (Hand-run Athena queries as `Admin`
succeeded, masking the problem — only the serve-role path 403s.)

## The fix — name the bucket `coa-*` (NO IAM change needed)

Create seed buckets as `coa-dev-<name>-<account>` (or any `coa-*`). The existing serve
role wildcard covers them automatically. We renamed to:

```
coa-dev-calanthir-pharma-seed-195698601974
```

## What a bucket rename cascades to — ONLY Glue table Locations

A COA source record stores the **Glue database name**, not a bucket:
```
configuration: {"catalogId":..., "region":..., "databaseName":"calanthir_pharma_seed"}
```
So the bucket is referenced in exactly ONE place: each Glue table's
`StorageDescriptor.Location`. To move buckets:

1. Copy objects bucket-to-bucket: `aws s3 cp s3://OLD/ s3://NEW/ --recursive` (server-side,
   not a local-file sync, so not blocked).
2. Re-point each Glue table's `Location` via `glue update-table` (preserve Columns/SerDe,
   swap only the bucket in Location).

The following do NOT change and need NO action:
- **Induced source record** (points at the Glue DB name)
- **Induced ontology / proposal / graph** (references classes + DB, not the bucket)
- **Per-namespace Athena workgroup** `coa-dev-<ns>` (where queries run, not where data lives)
- **Cedar/RRM grants** (`beacon-agents -> data-analyst`, namespace-scoped)
- **Athena results bucket** `coa-dev-athena-results-*` (already serve-readable)

No re-induction, no re-grant, no workgroup change.

## Alternative (if a bucket genuinely cannot be `coa-*`)

Add a read grant to the serve-agent role in `serve-agent-stack.ts` (CDK), scoped to the
bucket, so a fresh account inherits it. Prefer the `coa-*` name — it needs no IAM change
at all and is the platform's own convention. A `put-bucket-policy` from the agent session
is blocked by Kiro Crew policy; the CDK role grant or a `coa-*` name are the clean paths.

## Platform gap (worth a README/doc fix upstream)

`infra/README.md` documents the platform's own `coa-*` buckets but does NOT state that a
user-created seed/source bucket must be named `coa-*` to be serve-readable. Documenting
this convention (or having source-registration grant the serve role read on an approved
source's bucket) would prevent this trap on every new data drop.
