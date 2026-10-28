// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as cdk from "aws-cdk-lib";
import * as bedrockagentcore from "aws-cdk-lib/aws-bedrockagentcore";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as cr from "aws-cdk-lib/custom-resources";
import { DockerImageAsset, Platform } from "aws-cdk-lib/aws-ecr-assets";
import { Construct } from "constructs";
import { prefixedLambdaArn, resolveContext } from "../../context";
import { agentCoreSupportedSubnets } from "../../utils/agentcore-az";
import { brandEnv } from "../../utils/brand-env";
import { parseEcrImageUri } from "../../utils/ecr-utils";
import { Paths } from "../../paths";
import { SCLStack } from "../../constructs";
import { METRIC_API_FUNCTION_NAME } from "./metric-service-stack";
import { ONTOLOGY_API_PROXY_FUNCTION_NAME } from "./ontology-stack";

export interface AgentMcpStackProps extends cdk.StackProps {
  /** VPC for AgentCore Runtime network interfaces. */
  readonly vpc: ec2.IVpc;
  /** Roles DDB table for Cedar auth in the grant resolver. */
  readonly rolesTable: dynamodb.ITable;
  /** Resource-role-mappings DDB table for role resolution. */
  readonly resourceRoleMappingsTable: dynamodb.ITable;
  /** AgentCore-supported AZ names for subnet filtering. */
  readonly agentCoreAzNames?: string[];
  /**
   * The Loom M2M (client-credentials) client id that this wrapper's inbound JWT
   * authorizer trusts. Validated against the token's `client_id` claim via
   * `allowedClients` — NOT `aud` — because an M2M access token carries
   * `client_id` and has no user-audience `aud`. Supplied via the
   * `beacon_m2m_client_id` CDK context key (the client is created in the Loom
   * pool out-of-band; see specs/calanthir-pharmaceuticals/beacon-read/).
   */
  readonly beaconM2mClientId: string;
  /**
   * COA system groups that machine callers can be granted, one OAuth scope
   * each. When set, this stack creates a `coa` resource server on the Loom
   * pool with scopes `group.<g>` (full scope `coa/group.<g>`). An agent is
   * onboarded by granting its OWN M2M client the scope(s) in the IdP — no COA
   * change. Supplied via the `agent_mcp_scope_groups` context key (JSON array
   * or comma-separated string), e.g. ["beacon-agents","data-mapping-agents"].
   */
  readonly scopeGroups?: string[];
  /**
   * Switch authorization to scopes (`agent_mcp_enforce_scopes=true`). The
   * authorizer then admits any client holding a `coa/group.*` scope
   * (`allowedScopes`) instead of the single `allowedClients` entry, and the
   * container derives each caller's groups from its verified scopes. Deploy
   * with `scopeGroups` first, grant the scopes to the callers' clients, THEN
   * enable this, so existing callers are not locked out in between.
   */
  readonly enforceScopes?: boolean;
  /**
   * The Loom issuer discovery URL base (e.g.
   * https://cognito-idp.us-east-1.amazonaws.com/us-east-1_NoErXe1QA). The
   * authorizer appends `/.well-known/openid-configuration`. Read from the
   * `loom_issuer_url` context key or the `${ssmPrefix}/issuer` SSM param — the
   * SAME issuer the human runtimes trust, since the M2M client lives in the
   * same Loom pool.
   */
  readonly loomIssuerUrl?: string;
}

/**
 * Agent-MCP wrapper: an MCP runtime for a SYSTEM caller (e.g. Beacon) that
 * authenticates with a Loom M2M (client-credentials) token.
 *
 * THE ONE IDEA: the existing {@link McpStack} authorizer validates `aud`
 * (`allowedAudience=[clientId, mcpClientId]`) because it serves human ID tokens.
 * An M2M access token has NO user `aud` — it carries `client_id`. So this
 * wrapper's authorizer validates **`allowedClients=[beaconM2mClientId]`**
 * instead (verified viable in agentcore_jwt_authorizer_rules: allowedClients
 * checks client_id, which M2M tokens carry). That is the clean sidestep of the
 * "a machine token can't carry aud" wall.
 *
 * FLOW:
 *   Beacon --(Loom M2M token)--> THIS wrapper (validates client_id)
 *          --(IAM SigV4)--------> serve-agent runtime (IAM auth, no JWT)
 *          -------------------->  Neptune/Athena/OSS/Bedrock (governed cascade)
 *
 * The wrapper maps the VERIFIED M2M identity to the system profile
 * (groups=["beacon-agents"]) server-side, so groups come from the
 * cryptographically-validated token — not self-asserted in the body. This is
 * strictly stronger than a raw-SigV4 Shape 1 call (where profile.groups is only
 * trusted because the endpoint is locked down).
 *
 * The wrapper forwards to the serve-AGENT runtime (IAM SigV4), NOT the human
 * serve runtime, so serve's JWT authorizer is untouched by this path. The
 * container runs in MCP mode (SCL_MCP_MODE=true) with an added
 * SCL_CM_SIGV4=true switch so its execution tools SigV4-sign the CM invoke
 * instead of forwarding a bearer token. (That switch is the one serve-side code
 * change this path needs; tracked in the build plan.)
 */
export class AgentMcpStack extends SCLStack {
  public readonly agentRuntimeArn: string;

  constructor(scope: Construct, id: string, props: AgentMcpStackProps) {
    super(scope, id, props);
    this.addComponentTag("agent-mcp");

    const region = cdk.Stack.of(this).region;
    const account = cdk.Stack.of(this).account;
    const { ssmPrefix } = resolveContext(this.node);

    const runtimeName = this.prefixed("agent-mcp-server").replace(/-/g, "_");

    if (!props.beaconM2mClientId?.trim()) {
      throw new Error(
        "AgentMcpStack requires beaconM2mClientId (the Loom M2M client_id to " +
          "trust). Create the client in the Loom pool and pass it via the " +
          "`beacon_m2m_client_id` CDK context key.",
      );
    }

    // ── The serve-AGENT (IAM-authorized) runtime ARN, from SSM ────────
    // Written by ServeAgentStack. This wrapper forwards execution tools here.
    const serveAgentRuntimeArn = ssm.StringParameter.valueForStringParameter(
      this,
      `${ssmPrefix}/serve/agent-runtime-arn`,
    );

    // ── Discovery-tool backend Lambdas (describe_schema, list_metrics) ─
    // These tools invoke the metric-service and ontology-proxy Lambdas
    // DIRECTLY (not via the serve-agent HTTP path). The human MCP server
    // (mcp-stack) sets these; the wrapper must too, or describe_schema /
    // list_metrics fail with an empty-FunctionName boto3 validation error.
    // Built from the functions' fixed names (same as mcp-stack), so no
    // cross-stack dependency on metric-service / ontology stacks.
    const metricServiceLambdaArn = prefixedLambdaArn(
      this,
      METRIC_API_FUNCTION_NAME,
    );
    const ontologyProxyLambdaArn = prefixedLambdaArn(
      this,
      ONTOLOGY_API_PROXY_FUNCTION_NAME,
    );

    // ── Loom issuer (same pool the humans use; M2M client lives there) ─
    const issuerUrl =
      props.loomIssuerUrl ??
      ssm.StringParameter.valueForStringParameter(this, `${ssmPrefix}/issuer`);

    const groupClaimName = ssm.StringParameter.valueForStringParameter(
      this,
      `${ssmPrefix}/authentication-group-token-name`,
    );

    // ── COA scopes on the Loom pool (machine-caller authorization) ────
    // COA owns its own `coa` resource server, separate from Loom's `loom`
    // one. Each scope `coa/group.<g>` grants COA group <g> (Group::<g> in the
    // RRM tables). Agents are onboarded by granting their M2M client the scope
    // in the IdP, so the IdP is the single source of truth for who may do what.
    const SCOPE_RESOURCE_SERVER = "coa";
    const SCOPE_PREFIX = `${SCOPE_RESOURCE_SERVER}/group.`;
    const scopeGroups = [
      ...new Set((props.scopeGroups ?? []).map((g) => g.trim()).filter(Boolean)),
    ];
    for (const g of scopeGroups) {
      // Cognito scope names: printable ASCII without space, quote or backslash.
      if (!/^[A-Za-z0-9._-]+$/.test(g)) {
        throw new Error(`Invalid COA scope group name: ${JSON.stringify(g)}`);
      }
    }
    if (props.enforceScopes && scopeGroups.length === 0) {
      throw new Error(
        "agent_mcp_enforce_scopes requires agent_mcp_scope_groups (the scopes to admit).",
      );
    }
    if (scopeGroups.length > 0) {
      new cognito.CfnUserPoolResourceServer(this, "CoaScopesResourceServer", {
        // Pool id = last path segment of the issuer URL
        // (https://cognito-idp.<region>.amazonaws.com/<pool-id>).
        userPoolId: cdk.Fn.select(3, cdk.Fn.split("/", issuerUrl)),
        identifier: SCOPE_RESOURCE_SERVER,
        name: "Context Ontology Accelerator",
        scopes: scopeGroups.map((g) => ({
          scopeName: `group.${g}`,
          scopeDescription: `COA system group ${g}`,
        })),
      });
    }
    const allowedScopes = scopeGroups.map((g) => `${SCOPE_PREFIX}${g}`);

    // ── Security Group (HTTPS only; invokes serve-agent via NAT) ──────
    const mcpSg = new ec2.SecurityGroup(this, "AgentMcpSG", {
      vpc: props.vpc,
      description: "AgentCore Runtime - Agent MCP wrapper (M2M caller)",
      allowAllOutbound: false,
    });
    mcpSg.addEgressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      "HTTPS to VPC endpoints (AgentCore)",
    );
    mcpSg.addEgressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      "HTTPS to AgentCore invocations API (serve-agent) via NAT",
    );

    // ── Container image (shared) ──────────────────────────────────────
    const explicitImageUri = this.node.tryGetContext(
      "context_manager_image_uri",
    ) as string | undefined;
    let imageUri: string;
    let dockerAsset: DockerImageAsset | undefined;
    if (explicitImageUri) {
      imageUri = explicitImageUri;
    } else {
      dockerAsset = new DockerImageAsset(this, "AgentMcpImage", {
        directory: Paths.root,
        file: "packages/context-manager/Dockerfile",
        platform: Platform.LINUX_ARM64,
      });
      imageUri = dockerAsset.imageUri;
    }

    // ── AgentCore Runtime (MCP protocol, M2M/client_id authorizer) ────
    const runtime = new bedrockagentcore.Runtime(this, "AgentMcpRuntime", {
      runtimeName,
      agentRuntimeArtifact:
        bedrockagentcore.AgentRuntimeArtifact.fromImageUri(imageUri),
      protocolConfiguration: bedrockagentcore.ProtocolType.MCP,
      authorizerConfiguration:
        bedrockagentcore.RuntimeAuthorizerConfiguration.usingJWT(
          `${issuerUrl}/.well-known/openid-configuration`,
          // Scope mode: admit ANY Loom client whose M2M token carries a
          // coa/group.* scope (allowedScopes; at least one must match). Which
          // clients hold which scope is decided in the IdP, so onboarding an
          // agent needs no change here.
          //
          // Legacy mode: allowedClients validates the M2M token's `client_id`
          // (an M2M access token carries client_id, not a user aud).
          // AgentCore AND-verifies configured fields, so setting ONLY
          // allowedClients admits exactly the beacon-m2m client.
          props.enforceScopes ? undefined : [props.beaconM2mClientId],
          // allowedAudience intentionally undefined: M2M tokens have no user aud.
          undefined,
          props.enforceScopes ? allowedScopes : undefined,
        ),
      networkConfiguration:
        bedrockagentcore.RuntimeNetworkConfiguration.usingVpc(this, {
          vpc: props.vpc,
          vpcSubnets: {
            subnets: agentCoreSupportedSubnets(
              props.vpc,
              props.agentCoreAzNames,
            ),
          },
          securityGroups: [mcpSg],
        }),
      requestHeaderConfiguration: {
        allowlistedHeaders: ["Authorization"],
      },
      tracingEnabled: false,
      environmentVariables: {
        ...brandEnv(this.node),
        ENVIRONMENT: (this.node.tryGetContext("env") as string) ?? "dev",
        SSM_PREFIX: ssmPrefix,
        LOG_LEVEL: "INFO",
        AWS_REGION: region,
        ROLES_TABLE_NAME: props.rolesTable.tableName,
        RRM_TABLE_NAME: props.resourceRoleMappingsTable.tableName,
        GROUP_CLAIM_NAME: groupClaimName,
        // Execution tools forward to the serve-AGENT (IAM) runtime, not the
        // human serve runtime.
        CM_RUNTIME_ARN: serveAgentRuntimeArn,
        // Discovery tools (describe_schema, list_metrics) invoke these backend
        // Lambdas directly. Without them describe_schema fails with an
        // empty-FunctionName error. Same values the human MCP server sets.
        METRIC_SERVICE_LAMBDA_ARN: metricServiceLambdaArn,
        ONTOLOGY_PROXY_LAMBDA_ARN: ontologyProxyLambdaArn,
        SCL_MCP_MODE: "true",
        // Instruct the MCP execution tools to SigV4-sign the CM invoke (the
        // serve-agent runtime is IAM-authorized), instead of forwarding the
        // caller's bearer token. This is the one container-code switch the
        // M2M-wrapper path adds; see the build plan for the code change.
        SCL_CM_SIGV4: "true",
        // Map the verified M2M client identity to the system group server-side,
        // so groups are derived from the validated token, not self-asserted.
        SCL_SYSTEM_PROFILE_GROUPS: "beacon-agents",
        SCL_SYSTEM_PROFILE_USER_ID: "beacon-agent",
        // Scope mode: the container verifies the caller's M2M access token and
        // derives its COA groups from its coa/group.<g> scopes (userId = the
        // token's client_id). Unset in legacy mode -> the single profile above.
        ...(props.enforceScopes ? { SCL_SYSTEM_SCOPE_PREFIX: SCOPE_PREFIX } : {}),
        // Independent JWT verification in claims.py (defense-in-depth behind the
        // AgentCore authorizer). Same Loom issuer; the client is the M2M client.
        JWT_ISSUER_URL: issuerUrl,
        JWT_CLIENT_ID: props.beaconM2mClientId,
      },
      description:
        "Agent MCP wrapper - M2M (client_id) auth, SigV4-forwards to serve-agent",
    });

    // ── IAM ───────────────────────────────────────────────────────────
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ecr:GetAuthorizationToken"],
        resources: ["*"],
      }),
    );
    if (explicitImageUri) {
      const ecr = parseEcrImageUri(explicitImageUri);
      runtime.addToRolePolicy(
        new iam.PolicyStatement({
          actions: [
            "ecr:GetDownloadUrlForLayer",
            "ecr:BatchGetImage",
            "ecr:BatchCheckLayerAvailability",
          ],
          resources: [ecr.repositoryArn],
        }),
      );
    } else if (dockerAsset) {
      dockerAsset.repository.grantPull(runtime.role!);
    }

    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ssm:GetParameter"],
        resources: [`arn:aws:ssm:${region}:${account}:parameter${ssmPrefix}/*`],
      }),
    );

    // Invoke the serve-AGENT runtime (IAM SigV4). Scoped to the endpoint ARN
    // (the invoke targets the endpoint, not the bare runtime). This wrapper's
    // role must also be named in the serve-agent runtime's ENDPOINT
    // resource-policy — the security-critical single-caller boundary.
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock-agentcore:InvokeAgentRuntime"],
        resources: [
          `arn:aws:bedrock-agentcore:${region}:${account}:runtime/*`,
        ],
      }),
    );

    // Discovery tools invoke the metric-service and ontology-proxy Lambdas
    // directly. The env vars above name them; this grants the invoke. (Same
    // grant the human MCP server's role carries.)
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["lambda:InvokeFunction"],
        resources: [metricServiceLambdaArn, ontologyProxyLambdaArn],
      }),
    );

    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:BatchGetItem",
        ],
        resources: [
          props.rolesTable.tableArn,
          props.resourceRoleMappingsTable.tableArn,
          props.resourceRoleMappingsTable.tableArn + "/index/*",
        ],
      }),
    );

    this.agentRuntimeArn = runtime.agentRuntimeArn;

    // ── Lock the serve-agent endpoint to THIS wrapper's role ──────────
    // The serve-agent runtime is IAM-authorized and trusts profile.groups from
    // whoever reaches it, so its endpoint must admit exactly ONE SigV4 caller:
    // this wrapper. The policy names a principal (this wrapper's role) created by
    // THIS stack, while the endpoint belongs to serve-agent (which this stack
    // already depends on via the SSM read) — so the grant lives here, not on
    // serve-agent, avoiding a circular dependency. Done as an SDK custom resource
    // because put-resource-policy has no native L1 on the endpoint.
    //
    // NOTE: a resource policy is ADDITIVE (union with identity policies), so this
    // does not DENY other same-account principals; it is the cross-account-correct
    // admission grant and the single intended-caller record. A strict same-account
    // Deny is deliberately NOT added here (it would also block ops/Admin).
    const serveAgentEndpointArn = `${serveAgentRuntimeArn}/runtime-endpoint/DEFAULT`;
    const endpointPolicy = JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          Sid: "OnlyAgentMcpWrapperMayInvoke",
          Effect: "Allow",
          Principal: { AWS: runtime.role!.roleArn },
          Action: "bedrock-agentcore:InvokeAgentRuntime",
          Resource: serveAgentEndpointArn,
        },
      ],
    });
    // Fingerprint the policy into the physical id so onUpdate re-fires when the
    // wrapper role or endpoint changes (avoids the create-only staleness trap:
    // a custom resource with a static physical id is skipped on update).
    const policyFingerprint = cdk.Fn.base64(
      cdk.Fn.join("|", [runtime.role!.roleArn, serveAgentEndpointArn]),
    );
    const endpointLock = new cr.AwsCustomResource(this, "ServeAgentEndpointLock", {
      onCreate: {
        service: "bedrock-agentcore-control",
        action: "putResourcePolicy",
        parameters: { resourceArn: serveAgentEndpointArn, policy: endpointPolicy },
        physicalResourceId: cr.PhysicalResourceId.of(
          `serve-agent-endpoint-lock-${policyFingerprint}`,
        ),
      },
      onUpdate: {
        service: "bedrock-agentcore-control",
        action: "putResourcePolicy",
        parameters: { resourceArn: serveAgentEndpointArn, policy: endpointPolicy },
        physicalResourceId: cr.PhysicalResourceId.of(
          `serve-agent-endpoint-lock-${policyFingerprint}`,
        ),
      },
      // Scoped to exactly the one control-plane action this resource performs.
      // PutResourcePolicy is authorized by IAM against the RUNTIME arn (the parent
      // resource), even though the call's resourceArn is the ENDPOINT — so grant
      // both: the bare runtime arn (what IAM evaluates) and the endpoint arn.
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ["bedrock-agentcore:PutResourcePolicy"],
          resources: [serveAgentRuntimeArn, serveAgentEndpointArn],
        }),
      ]),
      installLatestAwsSdk: false,
    });
    endpointLock.node.addDependency(runtime);

    new ssm.StringParameter(this, "AgentMcpRuntimeArnParam", {
      parameterName: `${ssmPrefix}/agent-mcp/runtime-arn`,
      stringValue: runtime.agentRuntimeArn,
      description: "AgentCore Runtime ARN for the Agent MCP wrapper",
    });
    new ssm.StringParameter(this, "AgentMcpRuntimeRoleArnParam", {
      parameterName: `${ssmPrefix}/agent-mcp/runtime-role-arn`,
      stringValue: runtime.role!.roleArn,
      description:
        "Agent MCP wrapper runtime role ARN — name this in the serve-agent " +
        "endpoint resource-policy (the single trusted SigV4 caller).",
    });

    new cdk.CfnOutput(this, "AgentMcpRuntimeArn", {
      value: runtime.agentRuntimeArn,
      description: "Agent MCP wrapper AgentCore Runtime ARN",
    });
    new cdk.CfnOutput(this, "AgentMcpRuntimeId", {
      value: runtime.agentRuntimeId,
      description: "Agent MCP wrapper AgentCore Runtime ID",
    });
    new cdk.CfnOutput(this, "AgentMcpRuntimeRoleArn", {
      value: runtime.role!.roleArn,
      description:
        "Agent MCP wrapper role ARN (trusted SigV4 caller of serve-agent)",
    });
  }
}
