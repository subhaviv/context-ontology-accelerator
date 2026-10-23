#!/usr/bin/env bash
# XR-7 read-back through the M2M wrapper. Secrets never printed.
set -uo pipefail
SECRET_ARN='arn:aws:secretsmanager:us-east-1:172873868821:secret:beacon/coa-mcp-m2m-HBN6Xo'
NS=f054a82f-37a1-4033-a385-b9bf4d286056
ARN='arn:aws:bedrock-agentcore:us-east-1:195698601974:runtime/coa_dev_agent_mcp_server-LHhaJ29S2u'

SJSON=$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" --profile agentops-consumer --region us-east-1 --query SecretString --output text) || { echo "SECRET FAIL"; exit 1; }
CID=$(echo "$SJSON"|python3 -c "import sys,json;print(json.load(sys.stdin)['client_id'])")
CSEC=$(echo "$SJSON"|python3 -c "import sys,json;print(json.load(sys.stdin)['client_secret'])")
TEP=$(echo "$SJSON"|python3 -c "import sys,json;print(json.load(sys.stdin)['token_endpoint'])")
TOK=$(env -i PATH=/usr/bin:/bin curl -s -X POST "$TEP" -H "Content-Type: application/x-www-form-urlencoded" -d "grant_type=client_credentials&client_id=$CID&client_secret=$CSEC&scope=loom/mcp:read" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
[ -z "$TOK" ] && { echo "TOKEN FAIL"; exit 1; }
ARN_ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$ARN")
URL="https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/$ARN_ENC/invocations?qualifier=DEFAULT"
Q="${1:-Which trial sites are affected if material XR-7 API is delayed? Trace supplier to material to batch to drug to trial to sites.}"
BODY=$(python3 -c "import json,sys;print(json.dumps({'jsonrpc':'2.0','id':9,'method':'tools/call','params':{'name':'query','arguments':{'namespaceId':sys.argv[1],'query':sys.argv[2],'execute':True}}}))" "$NS" "$Q")
env -i PATH=/usr/bin:/bin curl -s -X POST "$URL" -H "authorization: Bearer $TOK" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d "$BODY" | python3 -c "import sys,json,re;raw=sys.stdin.read();m=re.search(r'data:\s*(\{.*\})',raw,re.S);d=json.loads(m.group(1)) if m else {'raw':raw[:500]};print(json.dumps(d.get('result',d),indent=1)[:2500])"
