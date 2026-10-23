#!/usr/bin/env bash
# Fire Beacon Q1-Q8 signal questions through the COA query tool. Secrets never printed.
set -uo pipefail
SECRET_ARN='arn:aws:secretsmanager:us-east-1:172873868821:secret:beacon/coa-mcp-m2m-HBN6Xo'
NS=f054a82f-37a1-4033-a385-b9bf4d286056
ARN='arn:aws:bedrock-agentcore:us-east-1:195698601974:runtime/coa_dev_agent_mcp_server-LHhaJ29S2u'
SJSON=$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --profile agentops-consumer --region us-east-1 --query SecretString --output text) || { echo SECRET FAIL; exit 1; }
CID=$(echo "$SJSON"|python3 -c "import sys,json;print(json.load(sys.stdin)['client_id'])")
CSEC=$(echo "$SJSON"|python3 -c "import sys,json;print(json.load(sys.stdin)['client_secret'])")
TEP=$(echo "$SJSON"|python3 -c "import sys,json;print(json.load(sys.stdin)['token_endpoint'])")
TOK=$(env -i PATH=/usr/bin:/bin curl -s -X POST "$TEP" -H "Content-Type: application/x-www-form-urlencoded" -d "grant_type=client_credentials&client_id=$CID&client_secret=$CSEC&scope=loom/mcp:read" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
[ -z "$TOK" ] && { echo TOKEN FAIL; exit 1; }
ARN_ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$ARN")
URL="https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/$ARN_ENC/invocations?qualifier=DEFAULT"

ask () {
  local id="$1"; local q="$2"
  local body
  body=$(python3 -c "import json,sys;print(json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'query','arguments':{'namespaceId':sys.argv[1],'query':sys.argv[2],'execute':True}}}))" "$NS" "$q")
  echo "===== $id ====="
  echo "Q: $q"
  env -i PATH=/usr/bin:/bin curl -s -X POST "$URL" -H "authorization: Bearer $TOK" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d "$body" \
    | python3 -c "
import sys,json,re
raw=sys.stdin.read()
m=re.search(r'data:\s*(\{.*\})',raw,re.S)
d=json.loads(m.group(1)) if m else {}
r=d.get('result',{})
t=r.get('content',[{}])[0].get('text','') if isinstance(r.get('content'),list) else ''
try: o=json.loads(t)
except: o={}
tier=o.get('tier'); conf=o.get('confidence',{}).get('score')
rows=o.get('resultRows', o.get('result'))
print(f'  tier={tier} confidence={conf}')
if isinstance(rows,list):
    print('  rows:', json.dumps(rows)[:400])
else:
    ans=o.get('synthesizedAnswer','') or str(rows)
    print('  answer/rows:', str(ans)[:400])
"
  echo
}

ask "Q1 expected_value (lead-time delay vs baseline)" "For material XR-7 Active Pharmaceutical Ingredient, what is its lead_time_days and baseline_lead_days?"
ask "Q2 affected (downstream enrolled patients)" "For any material whose name contains 'XR-7', total the enrolled participants across all trial sites of trials whose drug uses a batch of that material."
ask "Q3 kpi_tier (tier + clinical + drives chain)" "List all KPIs with their tier and clinical flag, and for each KPI which KPI it drives (from_kpi to to_kpi)."
ask "Q4 urgency (imminent events)" "List events with their date, severity, and event_type for materials or batches related to XR-7, ordered by date."
ask "Q5a source_authority / Q5b approver (who owns the KPI)" "Which persons own the KPIs named 'Batch Release On-Time Rate' or 'Release Assay Turnaround Time', with their role and org_unit?"
ask "Q6 novelty (prior facts)" "Is there any batch with release_test_status 'blocked_on_assay' or status 'qc_hold', and which material and drug does it relate to?"
ask "Q7 (measured_by anchor)" "Which entity (material, batch, trial, or site) is measured by the KPI 'Release Assay Turnaround Time'?"
ask "Q8 reuse (supplier qualification context)" "For the supplier of material XR-7, give its name, country, tier, qualified_status and risk_rating."
