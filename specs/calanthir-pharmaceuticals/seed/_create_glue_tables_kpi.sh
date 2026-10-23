#!/usr/bin/env bash
set -euo pipefail
PROFILE=agentops-publisher
REGION=us-east-1
DB=calanthir_pharma_seed
BUCKET=calanthir-pharma-seed-195698601974

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
        \"Location\": \"s3://$BUCKET/kpi/$prefix/\",
        \"InputFormat\": \"$INPUT\",
        \"OutputFormat\": \"$OUTPUT\",
        \"SerdeInfo\": {\"SerializationLibrary\": \"$SERDE\", \"Parameters\": {\"separatorChar\": \",\", \"quoteChar\": \"\\\"\"}}
      }
    }"
  echo "created $tbl"
}

c () {
  local out="["; local first=1
  for n in $1; do
    [ $first -eq 0 ] && out="$out,"; first=0
    out="$out{\"Name\":\"$n\",\"Type\":\"string\"}"
  done
  echo "$out]"
}

mk kpis        "$(c 'kpi_id name tier clinical unit target_value materiality_usd')" kpis
mk persons     "$(c 'person_id name role org_unit')" persons
mk measured_by "$(c 'measured_by_id kpi_id supplier_id material_id batch_id trial_id site_id')" measured_by
mk owns        "$(c 'owns_id person_id kpi_id')" owns
mk kpi_drives  "$(c 'drives_id from_kpi_id to_kpi_id')" kpi_drives
mk events      "$(c 'event_id name event_type date severity material_id batch_id site_id')" events

echo "ALL 6 KPI TABLES CREATED"
