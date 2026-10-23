# Note — Lake Formation-governed Glue assets & COA source registration

Context: seeding the `calanthir-pharma` namespace
(`f054a82f-37a1-4033-a385-b9bf4d286056`) from the Glue database
`calanthir_pharma_seed` (account `195698601974`, us-east-1). Recorded 2026-10-22.

## Two independent gates on a Glue source

Registering a Glue Data Catalog database as a COA source requires clearing **two
separate checks**. They are unrelated — satisfying one does nothing for the other.

### Gate 1 — COA's `coa:namespace` tag (ALWAYS required)
COA will only catalog a database whose owner has **opted in** by tagging it:

```
aws glue tag-resource \
  --resource-arn arn:aws:glue:us-east-1:195698601974:database/calanthir_pharma_seed \
  --tags-to-add '{"coa:namespace":"f054a82f-37a1-4033-a385-b9bf4d286056"}'
```

- Tag VALUE rules: **space-separated** list to share with several namespaces
  (Glue rejects commas in tag values); `ALL` to share with every namespace in the
  deployment.
- This is COA's own admission check, independent of AWS IAM/LF.

### Gate 2 — Lake Formation DESCRIBE (ONLY if the DB is LF-governed)
Whether this gate applies depends on the database's permission mode:

- **IAM mode (fall-back):** the resource carries a permission entry for the special
  principal `IAM_ALLOWED_PRINCIPALS`. LF then does NOT enforce — access is decided by
  plain IAM. COA's sources-api role already has the IAM permissions to read Glue, so
  **no LF grant is needed.** ← this is the case for `calanthir_pharma_seed`.
- **LF-governed:** the `IAM_ALLOWED_PRINCIPALS` entry is ABSENT. LF then denies every
  principal not explicitly granted, including COA's sources-api role. You must grant it
  LF `DESCRIBE` (and `SELECT` on the tables) before registration/scan will work.

## How to tell which mode a database is in

```
# Account default for newly-created databases:
aws lakeformation get-data-lake-settings \
  --query "DataLakeSettings.CreateDatabaseDefaultPermissions"
# If this shows IAM_ALLOWED_PRINCIPALS -> ALL, new DBs are born in IAM mode.

# Actual permissions on the specific database:
aws lakeformation list-permissions \
  --resource '{"Database":{"Name":"<db>"}}' \
  --query "PrincipalResourcePermissions[].{principal:Principal.DataLakePrincipalIdentifier,perms:Permissions}"
# Presence of IAM_ALLOWED_PRINCIPALS => IAM mode (no LF grant needed).
# Absence => LF-governed (grant DESCRIBE below).
```

`calanthir_pharma_seed` read back as:
```
arn:aws:iam::195698601974:role/Admin   -> ALL, ALTER, CREATE_TABLE, DESCRIBE, DROP
IAM_ALLOWED_PRINCIPALS                 -> ALL          <-- IAM mode, LF not enforced
```
Account default `CreateDatabaseDefaultPermissions` is also `IAM_ALLOWED_PRINCIPALS -> ALL`,
so a `aws glue create-database` here is born in IAM mode unless deliberately registered
with LF.

## If a future seed DB IS LF-governed — the grant

Two options the registration error surfaces:

```
# Option A — opt the whole DB back into IAM mode (simplest for a demo seed):
aws lakeformation grant-permissions \
  --principal DataLakePrincipalIdentifier=IAM_ALLOWED_PRINCIPALS \
  --resource '{"Database":{"Name":"<db>"}}' \
  --permissions DESCRIBE

# Option B — grant DESCRIBE to THIS deployment's sources-api role specifically
# (tighter; find the role via SSM /coa/sources/api-role-arn):
aws lakeformation grant-permissions \
  --principal DataLakePrincipalIdentifier=<sources-api-role-arn> \
  --resource '{"Database":{"Name":"<db>"}}' \
  --permissions DESCRIBE
# Plus SELECT/DESCRIBE on each table if table-level LF is in force.
```

## Takeaway

- For these demo seed DBs (created by plain `aws glue create-database` in this account),
  the DB is in **IAM mode** by default, so **only Gate 1 (the `coa:namespace` tag)**
  applies. LF is a conditional second gate that is not engaged here.
- If a real/customer data source is LF-governed, the tag alone is NOT enough — you must
  also grant the sources-api role LF `DESCRIBE`, or the COA scan fails with a permission
  error even though the tag is present.
