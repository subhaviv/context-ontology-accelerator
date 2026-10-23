#!/usr/bin/env bash
set -euo pipefail
PROFILE=agentops-publisher
REGION=us-east-1
DB=calanthir_pharma_seed
BUCKET=calanthir-pharma-seed-195698601974

# CSV SerDe with header skip
SERDE='org.apache.hadoop.hive.serde2.OpenCSVSerde'
INPUT='org.apache.hadoop.mapred.TextInputFormat'
OUTPUT='org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'

mk () {
  local tbl="$1"; local cols="$2"; local prefix="$3"
  aws glue create-table --database-name "$DB" --profile "$PROFILE" --region "$REGION" \
    --table-input "{
      \"Name\": \"$tbl\",
      \"TableType\": \"EXTERNAL_TABLE\",
      \"Parameters\": {\"classification\":\"csv\",\"skip.header.line.count\":\"1\",\"EXTERNAL\":\"TRUE\"},
      \"StorageDescriptor\": {
        \"Columns\": $cols,
        \"Location\": \"s3://$BUCKET/structural/$prefix/\",
        \"InputFormat\": \"$INPUT\",
        \"OutputFormat\": \"$OUTPUT\",
        \"SerdeInfo\": {\"SerializationLibrary\": \"$SERDE\", \"Parameters\": {\"separatorChar\": \",\", \"quoteChar\": \"\\\"\"}}
      }
    }"
  echo "created $tbl"
}

c () { # build json columns from "name1 name2 ..."
  local out="["; local first=1
  for n in $1; do
    [ $first -eq 0 ] && out="$out,"; first=0
    out="$out{\"Name\":\"$n\",\"Type\":\"string\"}"
  done
  echo "$out]"
}

mk suppliers   "$(c 'supplier_id name country tier qualified_status risk_rating')" suppliers
mk materials   "$(c 'material_id name material_type is_assay supplier_id lead_time_days baseline_lead_days gmp_grade')" materials
mk batches     "$(c 'batch_id material_id lot status quantity_kg release_test_status expiry_date')" batches
mk drugs       "$(c 'drug_id name modality batch_id therapeutic_area development_phase')" drugs
mk trials      "$(c 'trial_id name phase drug_id status primary_endpoint target_enrollment')" trials
mk trial_sites "$(c 'site_id trial_id region_id site_name enrolled next_dose_date supply_buffer_days')" trial_sites
mk regions     "$(c 'region_id name regulatory_body gmp_jurisdiction')" regions

echo "ALL 7 TABLES CREATED"
