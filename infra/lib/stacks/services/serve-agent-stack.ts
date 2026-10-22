// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as cdk from "aws-cdk-lib";
import * as bedrockagentcore from "aws-cdk-lib/aws-bedrockagentcore";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as opensearchserverless from "aws-cdk-lib/aws-opensearchserverless";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { DockerImageAsset, Platform } from "aws-cdk-lib/aws-ecr-assets";
import { Construct } from "constructs";
import { resolveContext } from "../../context";
import { agentCoreSupportedSubnets } from "../../utils/agentcore-az";
import { brandEnv } from "../../utils/brand-env";
import { parseEcrImageUri } from "../../utils/ecr-utils";
import { Paths } from "../../paths";
import { SCLStack } from "../../constructs";
import {
  DEFAULT_BEDROCK_LLM_MODEL_ID,
  DEFAULT_BEDROCK_MODEL_ID,
  DEFAULT_GRAPH_URI_BASE,
} from "../../constants";
import { TABLE_NAMES } from "@coa/shared";

export interface ServeAgentStackProps extends cdk.StackProps {
  /** VPC for AgentCore Runtime network interfaces. */
  readonly vpc: ec2.IVpc;
  /** AOSS VPC endpoint SG — AgentCore SG needs ingress here. */
  readonly aossSecurityGroup: ec2.ISecurityGroup;
  /** Neptune cluster SG — AgentCore SG needs ingress here (Gremlin/SPARQL). */
  readonly neptuneSecurityGroup: ec2.ISecurityGroup;
  /** Lambda SG (allowAllOutbound) for the AOSS proxy. */
  readonly lambdaSecurityGroup: ec2.ISecurityGroup;
  /** Neptune cluster ARN — graph store for ontology traversal. */
  readonly neptuneClusterArn: string;
  /** Neptune cluster writer endpoint (host for SigV4 Gremlin/SPARQL). */
  readonly neptuneEndpoint: string;
  /** S3 bucket ARN — compiled ontology artifacts (OWL, R2RML). */
  readonly ontologyBucketArn: string;
  /** VKG translation service endpoint. */
  readonly vkgEndpoint: string;
  /** S3 bucket name for Athena query results. */
  readonly athenaResultsBucketName?: string;
  /** Roles DDB table (authnz) — serve-path Cedar authorizer role policies. */
  readonly rolesTable: dynamodb.ITable;
  /** Resource-role-mappings DDB table (authnz) — principal→role resolution. */
  readonly resourceRoleMappingsTable: dynamodb.ITable;
  /** AgentCore-supported AZ names for subnet filtering. */
  readonly agentCoreAzNames?: string[];
  /** Bedrock LLM model ID for query resolution. */
  readonly bedrockLlmModelId?: string;
  /** Bedrock embedding model ID. */
  readonly bedrockEmbedModelId?: string;
  /** Comma-separated model IDs allowed for per-request override. */
  readonly allowedOverrideModels?: string[];
}

/**
 * Serve-AGENT layer: a SECOND Context Manager runtime for SYSTEM callers.
 *
 * Identical application image and orchestrator to {@link ServeStack}, with ONE
 * deliberate difference: its AgentCore inbound authorizer is **IAM SigV4**, not
 * a JWT authorizer. That is the whole point of this stack.
 *
 * WHY A SEPARATE RUNTIME (not a second authorizer on the human serve runtime):
 * an AgentCore runtime supports exactly ONE inbound auth method at a time
 * (AWS docs, runtime-security-best-practices: "A runtime can support one method
 * at a time; create separate versions for different authentication types"), and
 * one JWT authorizer cannot admit both human ID tokens (aud-based) and a machine
 * identity. So the human path (ServeStack, JWT) and the agent path (this stack,
 * IAM SigV4) MUST be distinct runtimes.
 *
 * WHY A SEPARATE STACK (not a second runtime inside serve-stack.ts): isolation.
 * serve-stack.ts is the load-bearing human query path; this agent runtime must
 * deploy/roll back on its OWN fate, so a failed agent-runtime deploy cannot
 * touch the human serve runtime.
 *
 * AUTH MODEL (Shape 1 / M2M-wrapper foundation): omitting `authorizerConfiguration`
 * makes the runtime authorize by IAM SigV4 — the default, per AWS docs. A caller
 * that holds `bedrock-agentcore:InvokeAgentRuntime` on this runtime's endpoint AND
 * is named in the endpoint resource-policy may invoke WITHOUT a JWT. serve's no-JWT
 * branch (proven live) then reads identity from the request body's `profile`
 * (userId/groups) and resolves the grant from the RRM table.
 *
 * SECURITY-CRITICAL: because the no-JWT branch TRUSTS profile.groups as asserted,
 * this runtime's endpoint resource-policy MUST name exactly ONE trusted caller
 * (the agent-MCP wrapper's role, or Beacon's role directly). If any
 * less-trusted principal could invoke it, that principal could assert
 * groups:['Admin'] and escalate. The endpoint policy is the real boundary.
 *
 * Writes `${ssmPrefix}/serve/agent-runtime-arn` for the agent-MCP wrapper to read.
 */
export class ServeAgentStack extends SCLStack {
  public readonly agentRuntimeArn: string;
  public readonly runtimeRoleArn: string;

  constructor(scope: Construct, id: string, props: ServeAgentStackProps) {
    super(scope, id, props);
    this.addComponentTag("serve-agent");

    const region = cdk.Stack.of(this).region;
    const account = cdk.Stack.of(this).account;
    const { prefix, ssmPrefix } = resolveContext(this.node);

    // ── Config via SSM (same keys ServeStack reads) ──────────────────
    const opensearchEndpoint = ssm.StringParameter.valueForStringParameter(
      this,
      `${ssmPrefix}/opensearch/endpoint`,
    );
    const opensearchCollectionName =
      ssm.StringParameter.valueForStringParameter(
        this,
        `${ssmPrefix}/opensearch/collection-name`,
      );
    const opensearchCollectionArn = ssm.StringParameter.valueForStringParameter(
      this,
      `${ssmPrefix}/opensearch/collection-arn`,
    );
    const namespacesTableName = ssm.StringParameter.valueForStringParameter(
      this,
      `${ssmPrefix}/namespace/namespaces-table-name`,
    );
    // The group claim name — present for parity with ServeStack even though the
    // IAM/no-JWT path resolves groups from profile.groups (body), not a JWT claim.
    const groupClaimName = ssm.StringParameter.valueForStringParameter(
      this,
      `${ssmPrefix}/authentication-group-token-name`,
    );

    const dataSourcesTableName = this.prefixed(TABLE_NAMES.SOURCES);
    const dataSourcesTableArn = `arn:aws:dynamodb:${region}:${account}:table/${dataSourcesTableName}`;
    const namespacesTableArn = `arn:aws:dynamodb:${region}:${account}:table/${namespacesTableName}`;

    // Distinct runtime name from the human serve runtime.
    const runtimeName = this.prefixed("context-manager-agent").replace(
      /-/g,
      "_",
    );

    // ── Security Group (same egress shape as ServeStack's AgentCoreSG) ──
    const agentCoreSg = new ec2.SecurityGroup(this, "ServeAgentSG", {
      vpc: props.vpc,
      description: "AgentCore Runtime - Context Manager (SYSTEM/IAM caller)",
      allowAllOutbound: false,
    });
    agentCoreSg.addEgressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      "HTTPS to VPC endpoints",
    );
    agentCoreSg.addEgressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      "HTTPS to external services via NAT",
    );
    agentCoreSg.addEgressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(8182),
      "Neptune within VPC",
    );
    agentCoreSg.addEgressRule(
      ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
      ec2.Port.tcp(8080),
      "VKG service within VPC",
    );
    for (const port of [5432, 3306, 1433, 5439]) {
      agentCoreSg.addEgressRule(
        ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
        ec2.Port.tcp(port),
        `JDBC port ${port} within VPC`,
      );
    }

    // AgentCore → AOSS VPC endpoint (L1 to avoid cross-stack cycle)
    new ec2.CfnSecurityGroupIngress(this, "AossVpceIngress", {
      groupId: props.aossSecurityGroup.securityGroupId,
      ipProtocol: "tcp",
      fromPort: 443,
      toPort: 443,
      sourceSecurityGroupId: agentCoreSg.securityGroupId,
      description: "Allow HTTPS from serve-agent AgentCore to AOSS VPC endpoint",
    });
    // AgentCore → Neptune
    new ec2.CfnSecurityGroupIngress(this, "NeptuneIngress", {
      groupId: props.neptuneSecurityGroup.securityGroupId,
      ipProtocol: "tcp",
      fromPort: 8182,
      toPort: 8182,
      sourceSecurityGroupId: agentCoreSg.securityGroupId,
      description: "Allow Neptune Gremlin/SPARQL from serve-agent runtime",
    });

    // ── Container image (shared with ServeStack/McpStack) ─────────────
    const explicitImageUri = this.node.tryGetContext(
      "context_manager_image_uri",
    ) as string | undefined;
    let imageUri: string;
    let dockerAsset: DockerImageAsset | undefined;
    if (explicitImageUri) {
      imageUri = explicitImageUri;
    } else {
      dockerAsset = new DockerImageAsset(this, "ContextManagerAgentImage", {
        directory: Paths.root,
        file: "packages/context-manager/Dockerfile",
        platform: Platform.LINUX_ARM64,
      });
      imageUri = dockerAsset.imageUri;
    }

    // ── AgentCore Memory (session state, as ServeStack) ───────────────
    const memory = new bedrockagentcore.Memory(this, "SessionMemory", {
      memoryName: runtimeName + "_memory",
      description: "Conversation history for system-caller sessions",
      expirationDuration: cdk.Duration.days(30),
    });

    // ── The runtime — IAM SigV4 auth (NO authorizerConfiguration) ─────
    const runtime = new bedrockagentcore.Runtime(this, "ServeAgentRuntime", {
      runtimeName,
      agentRuntimeArtifact:
        bedrockagentcore.AgentRuntimeArtifact.fromImageUri(imageUri),
      // DELIBERATELY NO authorizerConfiguration: AgentCore defaults to IAM SigV4
      // inbound auth (AWS docs). This is the system-caller door — a SigV4-signed
      // InvokeAgentRuntime from a principal named in the endpoint resource-policy
      // is admitted WITHOUT a JWT, and serve's no-JWT branch reads profile.groups.
      networkConfiguration:
        bedrockagentcore.RuntimeNetworkConfiguration.usingVpc(this, {
          vpc: props.vpc,
          vpcSubnets: {
            subnets: agentCoreSupportedSubnets(
              props.vpc,
              props.agentCoreAzNames,
            ),
          },
          securityGroups: [agentCoreSg],
        }),
      environmentVariables: {
        ...brandEnv(this.node),
        ENVIRONMENT: (this.node.tryGetContext("env") as string) ?? "dev",
        SSM_PREFIX: ssmPrefix,
        LOG_LEVEL: "INFO",
        AWS_REGION: region,
        NEPTUNE_ENDPOINT: props.neptuneEndpoint,
        VKG_ENDPOINT: props.vkgEndpoint,
        OPENSEARCH_ENDPOINT: opensearchEndpoint,
        ATHENA_OUTPUT_S3: `s3://${props.athenaResultsBucketName ?? this.prefixed("athena-results")}/`,
        ATHENA_WORKGROUP_PREFIX: `${this.prefixed("")}`,
        OSS_ONTOLOGY_INDEX: opensearchCollectionName,
        TIER3_STRATEGY:
          (this.node.tryGetContext("tier3_strategy") as string) ??
          "lexical-baseline",
        LEXICAL_RETRIEVER_STRATEGY:
          (this.node.tryGetContext("lexical_retriever_strategy") as string) ??
          "topic_beam",
        RESOLVE_TIMEOUT_S:
          (this.node.tryGetContext("resolve_timeout_s") as string) ?? "170",
        GRAPH_URI_TEMPLATE: `${DEFAULT_GRAPH_URI_BASE}/{namespace}`,
        DATA_SOURCES_TABLE: dataSourcesTableName,
        NAMESPACES_TABLE: namespacesTableName,
        ROLES_TABLE_NAME: props.rolesTable.tableName,
        RRM_TABLE_NAME: props.resourceRoleMappingsTable.tableName,
        GROUP_CLAIM_NAME: groupClaimName,
        MEMORY_ID: memory.memoryId,
        // prod leaves this UNSET so a role-less caller fails closed; the agent
        // path always carries a resolved group grant, so no fail-open is needed.
        ALLOW_NO_GUARDRAIL: this.envName !== "prod" ? "true" : "false",
        BEDROCK_MODEL_ID:
          props.bedrockLlmModelId ?? DEFAULT_BEDROCK_LLM_MODEL_ID,
        BEDROCK_EMBED_MODEL_ID:
          props.bedrockEmbedModelId ?? DEFAULT_BEDROCK_MODEL_ID,
        ...(props.allowedOverrideModels?.length && {
          ALLOWED_OVERRIDE_MODELS: props.allowedOverrideModels.join(","),
        }),
      },
      description:
        "Context Manager (system/IAM caller) — IAM SigV4 inbound auth, no JWT",
    });

    memory.grantWrite(runtime);
    memory.grantRead(runtime);

    this.agentRuntimeArn = runtime.agentRuntimeArn;
    this.runtimeRoleArn = runtime.role!.roleArn;

    // ── IAM grants (same data-plane reads ServeStack's runtime role has) ──
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
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:Converse",
          "bedrock:ConverseStream",
          "bedrock:ApplyGuardrail",
        ],
        resources: [
          `arn:aws:bedrock:*:${account}:inference-profile/*`,
          `arn:aws:bedrock:*::foundation-model/*`,
          `arn:aws:bedrock:${region}:${account}:guardrail/*`,
        ],
      }),
    );
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["neptune-db:ReadDataViaQuery"],
        resources: [props.neptuneClusterArn],
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
          dataSourcesTableArn,
          dataSourcesTableArn + "/index/*",
          namespacesTableArn,
          props.rolesTable.tableArn,
          props.resourceRoleMappingsTable.tableArn,
          props.resourceRoleMappingsTable.tableArn + "/index/*",
        ],
      }),
    );
    const athenaResultsBucket =
      props.athenaResultsBucketName ?? this.prefixed("athena-results");
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "s3:GetObject",
          "s3:ListBucket",
          "s3:PutObject",
          "s3:GetBucketLocation",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts",
          "s3:DeleteObject",
        ],
        resources: [
          props.ontologyBucketArn,
          props.ontologyBucketArn + "/*",
          `arn:aws:s3:::${athenaResultsBucket}`,
          `arn:aws:s3:::${athenaResultsBucket}/*`,
          `arn:aws:s3:::${prefix}-*`,
          `arn:aws:s3:::${prefix}-*/*`,
        ],
      }),
    );
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:GetWorkGroup",
          "athena:GetDataCatalog",
        ],
        resources: [
          `arn:aws:athena:${region}:${account}:workgroup/*`,
          `arn:aws:athena:${region}:${account}:datacatalog/*`,
        ],
      }),
    );
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "glue:GetCatalog",
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetPartitions",
          "glue:GetUnfilteredTableMetadata",
          "glue:GetUnfilteredPartitionMetadata",
        ],
        resources: [
          `arn:aws:glue:${region}:${account}:catalog`,
          `arn:aws:glue:${region}:${account}:catalog/*`,
          `arn:aws:glue:${region}:${account}:database/*`,
          `arn:aws:glue:${region}:${account}:table/*/*`,
        ],
      }),
    );
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["lakeformation:GetDataAccess"],
        resources: ["*"],
      }),
    );

    // ── Direct AOSS access for the lexical-baseline retriever ─────────
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["aoss:APIAccessAll"],
        resources: [opensearchCollectionArn],
      }),
    );
    new opensearchserverless.CfnAccessPolicy(this, "OSSServeAgentDataAccess", {
      name: this.prefixed("serve-agent-ro"),
      type: "data",
      description: "Read-only AOSS data access for the serve-agent runtime",
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: "index",
              Resource: [`index/${opensearchCollectionName}/*`],
              Permission: ["aoss:DescribeIndex", "aoss:ReadDocument"],
            },
            {
              ResourceType: "collection",
              Resource: [`collection/${opensearchCollectionName}`],
              Permission: ["aoss:DescribeCollectionItems"],
            },
          ],
          Principal: [runtime.role!.roleArn],
        },
      ]),
    });

    // ── SSM: publish the agent runtime ARN for the agent-MCP wrapper ──
    new ssm.StringParameter(this, "ServeAgentRuntimeArnParam", {
      parameterName: `${ssmPrefix}/serve/agent-runtime-arn`,
      stringValue: runtime.agentRuntimeArn,
      description:
        "AgentCore Runtime ARN for the system/IAM-authorized Context Manager",
    });
    new ssm.StringParameter(this, "ServeAgentRuntimeRoleArnParam", {
      parameterName: `${ssmPrefix}/serve/agent-runtime-role-arn`,
      stringValue: runtime.role!.roleArn,
      description: "serve-agent runtime IAM role ARN",
    });

    new cdk.CfnOutput(this, "ServeAgentRuntimeArn", {
      value: runtime.agentRuntimeArn,
      description: "serve-agent AgentCore Runtime ARN (IAM SigV4 auth)",
    });
    new cdk.CfnOutput(this, "ServeAgentRuntimeId", {
      value: runtime.agentRuntimeId,
      description: "serve-agent AgentCore Runtime ID",
    });
  }
}
