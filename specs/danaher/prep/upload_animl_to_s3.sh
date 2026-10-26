#!/usr/bin/env bash
# Upload the AnIML CSVs into their Glue table S3 prefixes, then verify.
# Namespace: animl-afo (3a6c6a53-af55-43fe-bcb9-8ea7ebee55e9) in agentops-publisher (195698601974).
# Infra (bucket, Glue DB tagged coa:namespace, 3 tables) is already created.
# You run this (local-file S3 upload is yours). Idempotent: re-running just overwrites.
set -euo pipefail

PROFILE="agentops-publisher"
REGION="us-east-1"
BUCKET="coa-dev-animl-afo-seed-195698601974"
DATA_DIR="/Users/subhaviv/Documents/src/context-ontology-accelerator/specs/danaher/prep/animl_data"

echo "Uploading AnIML CSVs to s3://$BUCKET/structural/<table>/ ..."
for t in samples experiment_steps measurements; do
  src="$DATA_DIR/$t.csv"
  dst="s3://$BUCKET/structural/$t/$t.csv"
  if [ ! -f "$src" ]; then
    echo "  MISSING local file: $src" >&2
    exit 1
  fi
  aws s3 cp "$src" "$dst" --profile "$PROFILE" --region "$REGION"
done

echo ""
echo "Verifying objects landed (one CSV per table prefix):"
for t in samples experiment_steps measurements; do
  echo "  structural/$t/:"
  aws s3 ls "s3://$BUCKET/structural/$t/" --profile "$PROFILE" --region "$REGION"
done

echo ""
echo "Done. Next in COA (https://d3upg0x9odcmsc.cloudfront.net):"
echo "  1. Register structured source -> Glue DB 'animl_afo_seed' in namespace 'animl-afo'"
echo "  2. Approve the source + its 3 tables (PENDING_REVIEW -> APPROVED)"
echo "  3. Upload animl_ontology.owl as a reference ontology"
echo "  4. Induce: strategy=table_to_ontology, grounding_mode=ENHANCED,"
echo "     grounding ontology = AnIML (http://www.w3id.org/animl/ontology/)"
echo "  5. Open matches.json"
