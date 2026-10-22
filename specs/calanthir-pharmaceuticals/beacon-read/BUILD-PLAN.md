# Beacon → COA governed read — build plan

**Decision:** Beacon (a background AgentCore runtime in the CONSUMER account) reads
COA's governed graph through an **M2M-token MCP wrapper**, NOT a cross-account IAM
resource policy. The two AWS accounts share only a **Loom identity** relationship;
no publisher resource names a consumer-account IAM principal.

## Accounts & identities (REAL values)

| Thing | Value |
|---|---|
| Publisher account (COA, Loom pool, serve, MCP) | `195698601974` |
| Consumer account (Beacon runtime + its secret) | `172873868821` |
| Loom user pool | `us-east-1_NoErXe1QA` |
| Loom hosted-UI domain | `loom-agentops-195698601974` |
| Loom token endpoint | `https://loom-agentops-195698601974.auth.us-east-1.amazoncognito.com/oauth2/token` |
| **beacon-m2m client id** | **`3eodl9tehbne84sosoqqvnhqf4`** |
| beacon-m2m flow / scope | `client_credentials` / `loom/mcp:read` |
| **Beacon secret (consumer account)** | **`arn:aws:secretsmanager:us-east-1:172873868821:secret:beacon/coa-mcp-m2m-HBN6Xo`** (name `beacon/coa-mcp-m2m`) — JSON `{client_id, client_secret, token_endpoint, scope}` |
| pharmacy namespace id | `36521996-76e9-433f-96cb-d2ec2db4e568` |
| RRM grant (written live) | `Group::beacon-agents -> data-analyst` on the pharmacy namespace |

## The flow (no cross-account IAM policy)

```
Beacon (consumer runtime)
  - reads beacon/coa-mcp-m2m from ITS OWN account's Secrets Manager
  - POST Loom /oauth2/token  (client_credentials, client_id+secret, scope=loom/mcp:read)
      -> Loom M2M access token  (carries client_id=3eodl9tehbne84sosoqqvnhqf4, no user aud)
  - Authorization: Bearer <M2M token>  ->  COA agent-MCP wrapper (publisher)
        agent-MCP authorizer validates allowedClients=[3eodl9tehbne84sosoqqvnhqf4]  (client_id, not aud)
        agent-MCP maps the VERIFIED client -> system profile {userId:beacon-agent, groups:[beacon-agents]}
  - agent-MCP  --SigV4 (its own publisher role)-->  serve-agent runtime (publisher, IAM auth, no JWT)
        serve no-JWT branch reads profile.groups -> resolve_profile -> Group::beacon-agents -> data-analyst
        governed Tier-1/2/3 cascade over the pharmacy namespace
```

Both cross-account-looking hops are NOT IAM cross-account:
- Beacon -> agent-MCP is a **Loom JWT** hop (client_id trust), not IAM.
- agent-MCP -> serve-agent is **same-account** (both publisher) SigV4.

## Status

DONE
- `beacon-m2m` Loom client created (client_credentials, `loom/mcp:read`).
- Secret `beacon/coa-mcp-m2m` stored in the consumer account (value never echoed).
- RRM grant `Group::beacon-agents -> data-analyst` on pharmacy — written live, resolves
  via the no-JWT path (proven with serve's real `resolve_profile`).
- CDK: `serve-agent-stack.ts` (IAM-authorized serve runtime) + `agent-mcp-stack.ts`
  (MCP wrapper, allowedClients authorizer, SigV4-forwards to serve-agent). Wired in
  `app.ts`; agent-mcp gated on the `beacon_m2m_client_id` context key. tsc green.
- Container code: `SCL_CM_SIGV4` path in `ContextManagerClient` (signs the CM invoke
  with the wrapper's role instead of forwarding a bearer token) + server builds the
  system profile from `SCL_SYSTEM_PROFILE_*` config on that path. 174 MCP unit tests green.

REMAINING
1. Deploy `coa-dev-serve-agent` (cdk diff first). Writes `/coa/serve/agent-runtime-arn`.
2. Deploy `coa-dev-agent-mcp` with `-c beacon_m2m_client_id=3eodl9tehbne84sosoqqvnhqf4`.
3. serve-agent ENDPOINT resource-policy: name the **agent-mcp wrapper's runtime role**
   (publisher principal, from the agent-mcp stack output) as the sole SigV4 invoker.
   Same-account — no consumer principal named.
4. Beacon side (consumer account):
   - grant Beacon's runtime role `secretsmanager:GetSecretValue` on the secret ARN
     (same-account).
   - add a tool: read secret -> mint Loom M2M token -> call the agent-MCP wrapper.
5. Live test: Beacon mints a token and calls agent-MCP -> governed answer over pharmacy.

## Notes
- Reusable shared Loom M2M client `752qo4e554h96dj9352p930llb` exists but a DEDICATED
  `beacon-m2m` was created for isolation (revocable without touching other M2M consumers).
- COA Cognito pool is dead (repoint); everything is Loom + IAM.
