// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as fs from "fs";
import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { TABLE_NAMES } from "@coa/shared";
import { Construct } from "constructs";
import { prefixedLambdaArn, resolveContext } from "../../context";
import { fromRoot } from "../../paths";
import { SCLStack } from "../../constructs";
import { bundlePython } from "../../utils/python-bundling";
import { METRIC_API_FUNCTION_NAME } from "./metric-service-stack";
import { ONTOLOGY_API_PROXY_FUNCTION_NAME } from "./ontology-stack";

const SERVE_TOOL_DIR = "packages/context-manager/lambda/serve-tool";
const DISCOVERY_TOOL_DIR = "packages/context-manager/lambda/discovery-tool";

/**
 * What each discovery tool reads: a backend Lambda it invokes directly, or a
 * table it reads one item from (both by fixed prefixed name).
 */
const DISCOVERY_BACKENDS: Record<
  string,
  { lambda: string } | { table: string }
> = {
  list_metrics: { lambda: METRIC_API_FUNCTION_NAME },
  describe_schema: { lambda: ONTOLOGY_API_PROXY_FUNCTION_NAME },
  // Not the control-plane namespace Lambda: it also serves PUT/DELETE and
  // trusts API Gateway's authorizer, so invoking it would be write access.
  get_namespace: { table: TABLE_NAMES.NAMESPACES },
};

/** Default COA identity every agent-tool call runs as. */
export const DEFAULT_AGENT_USER_ID = "factory-agent";
export const DEFAULT_AGENT_GROUPS = ["factory-agents"];

/** Tool names from `tool-schemas.json`, in file order. */
export function serveToolNames(): string[] {
  return toolNamesIn(SERVE_TOOL_DIR);
}

/** Discovery tool names from `discovery-tool/tool-schemas.json`. */
export function discoveryToolNames(): string[] {
  return toolNamesIn(DISCOVERY_TOOL_DIR);
}

function toolNamesIn(dir: string): string[] {
  return (
    JSON.parse(
      fs.readFileSync(fromRoot(`${dir}/tool-schemas.json`), "utf8"),
    ) as { name: string }[]
  ).map((t) => t.name);
}

/** `translate_sparql` -> `TranslateSparql`, for construct ids and outputs. */
const pascal = (name: string): string =>
  name
    .split(/[_-]/)
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join("");

export interface ServeToolStackProps extends cdk.StackProps {
  /**
   * Principals allowed to invoke the `:PROD` aliases. Empty by default: factory
   * gateways get their invoke grant from the factory's registry stack on the
   * alias, so none are hardcoded here.
   */
  readonly invokerPrincipalArns?: string[];
  /** COA user id agent-tool calls run as. Default {@link DEFAULT_AGENT_USER_ID}. */
  readonly agentUserId?: string;
  /** COA groups agent-tool calls run as. Default {@link DEFAULT_AGENT_GROUPS}. */
  readonly agentGroups?: string[];
}

/**
 * M2M Lambda tools for COA, for AI Agent Factory gateway Lambda targets.
 *
 * - Agent tools: ONE function per tool in `tool-schemas.json`
 *   (`serve-tool-<tool>`), each with its own `PROD` alias so every tool has a
 *   unique target ARN (the factory catalogue rejects two tools sharing one).
 *   Each calls serve-agent (IAM) with SigV4 and may InvokeAgentRuntime on
 *   serve-agent only. Every call runs as one fixed COA identity from props;
 *   admission is the calling platform's job, serve-agent's grants authorize the
 *   identity per namespace.
 * - Discovery tools (`list_metrics`, `describe_schema`, `get_namespace`): one
 *   function per tool as well. The first two call the metric-service /
 *   ontology-proxy Lambda directly; `get_namespace` reads the namespace's
 *   metadata item. Those backends do not authorize callers, so these functions
 *   run COA's grant lookup and Cedar `viewNamespace` check for the same fixed
 *   identity first.
 *   Schemas in `discovery-tool/tool-schemas.json`.
 *
 * Everything lives in the same account as serve and the discovery backends, so
 * no hop crosses an account.
 */
export class ServeToolStack extends SCLStack {
  /** Agent-tool `PROD` aliases, keyed by tool name. */
  public readonly toolAliases: Record<string, lambda.Alias> = {};

  constructor(scope: Construct, id: string, props: ServeToolStackProps = {}) {
    super(scope, id, props);
    this.addComponentTag("serve-tool");

    const { ssmPrefix } = resolveContext(this.node);
    const agentUserId = props.agentUserId ?? DEFAULT_AGENT_USER_ID;
    const agentGroups = props.agentGroups ?? DEFAULT_AGENT_GROUPS;
    if (!agentUserId || agentGroups.length === 0) {
      throw new Error(
        "ServeToolStack: agentUserId and agentGroups must be non-empty",
      );
    }

    const serveAgentRuntimeArn = ssm.StringParameter.valueForStringParameter(
      this,
      `${ssmPrefix}/serve/agent-runtime-arn`,
    );

    // No pip deps: the handler uses only stdlib + botocore from the runtime.
    const code = bundlePython({
      srcDirs: [fromRoot(SERVE_TOOL_DIR)],
      architecture: "arm64",
    });

    const makeFn = (
      idBase: string,
      functionSuffix: string,
      description: string,
      environment: Record<string, string>,
      fnOpts: {
        code: lambda.Code;
        handler: string;
        timeout: cdk.Duration;
      } = {
        code,
        handler: "serve_tool_handler.handler",
        // Serve's own resolve budget is 170s; leave headroom for the hop.
        timeout: cdk.Duration.seconds(180),
      },
    ): lambda.Alias => {
      const fn = new lambda.Function(this, `${idBase}Fn`, {
        functionName: this.prefixed(functionSuffix),
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.ARM_64,
        handler: fnOpts.handler,
        code: fnOpts.code,
        timeout: fnOpts.timeout,
        memorySize: 256,
        environment,
        description,
      });
      // currentVersion re-publishes when code or config changes, so PROD
      // always points at what this deploy shipped.
      const alias = new lambda.Alias(this, `${idBase}ProdAlias`, {
        aliasName: "PROD",
        version: fn.currentVersion,
      });
      for (const [i, arn] of (props.invokerPrincipalArns ?? []).entries()) {
        alias.addPermission(`${idBase}Invoker${i}`, {
          principal: new iam.ArnPrincipal(arn),
          action: "lambda:InvokeFunction",
        });
      }
      return alias;
    };

    for (const tool of serveToolNames()) {
      const idBase = `Tool${pascal(tool)}`;
      const alias = makeFn(
        idBase,
        `serve-tool-${tool.replace(/_/g, "-")}`,
        `COA agent tool '${tool}' -> serve-agent (IAM)`,
        {
          SERVE_INVOKE_TIMEOUT_S: "170",
          SERVE_TOOL_NAME: tool,
          SERVE_RUNTIME_ARN: serveAgentRuntimeArn,
          COA_AGENT_USER_ID: agentUserId,
          COA_AGENT_GROUPS: agentGroups.join(","),
        },
      );
      // Invoke serve-agent only: the bare runtime and its endpoints.
      alias.lambda.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ["bedrock-agentcore:InvokeAgentRuntime"],
          resources: [serveAgentRuntimeArn, `${serveAgentRuntimeArn}/*`],
        }),
      );
      this.toolAliases[tool] = alias;
      new cdk.CfnOutput(this, `ToolAliasArn${pascal(tool)}`, {
        value: alias.functionArn,
        description: `Agent tool '${tool}' :PROD alias (serve-agent, IAM)`,
      });
    }

    // ── Discovery tools (list_metrics, describe_schema) ───────────────
    // These call the metric-service / ontology-proxy Lambda directly (as the
    // MCP server does), and those backends do not authorize the caller, so the
    // handler runs COA's grant lookup + Cedar viewNamespace check itself. It
    // needs coa_serve.role_resolver and coa_authorization (+ cedarpy).
    const discoveryCode = bundlePython({
      srcDirs: [
        fromRoot(DISCOVERY_TOOL_DIR),
        fromRoot("packages/context-manager/src"),
        fromRoot("libs/common/src"),
      ],
      // Same set as the other Lambdas that bundle libs/common. Bare names so
      // the local bundling shell does not read `>=` as a redirect.
      pipDeps: [
        "cedarpy",
        "cryptography",
        "opensearch-py",
        "pydantic",
        "pydantic-settings",
        "PyJWT",
        "requests",
        "requests-aws4auth",
        "structlog",
      ],
      architecture: "arm64",
    });
    const rolesTableName = this.prefixed(TABLE_NAMES.ROLES);
    const rrmTableName = this.prefixed(TABLE_NAMES.RESOURCE_ROLE_MAPPINGS);
    const tableArn = (name: string) =>
      this.formatArn({
        service: "dynamodb",
        resource: "table",
        resourceName: name,
      });

    for (const tool of discoveryToolNames()) {
      const backend = DISCOVERY_BACKENDS[tool];
      if (!backend) {
        throw new Error(
          `ServeToolStack: no backend for discovery tool '${tool}'`,
        );
      }
      const backendName = "lambda" in backend ? backend.lambda : backend.table;
      const backendEnv: Record<string, string> =
        "lambda" in backend
          ? { BACKEND_LAMBDA_ARN: prefixedLambdaArn(this, backend.lambda) }
          : { NAMESPACES_TABLE: this.prefixed(backend.table) };
      const idBase = `Tool${pascal(tool)}`;
      const alias = makeFn(
        idBase,
        `serve-tool-${tool.replace(/_/g, "-")}`,
        `COA agent tool '${tool}' -> ${backendName} (grant + Cedar checked here)`,
        {
          DISCOVERY_TOOL_NAME: tool,
          ...backendEnv,
          COA_AGENT_USER_ID: agentUserId,
          COA_AGENT_GROUPS: agentGroups.join(","),
          ROLES_TABLE_NAME: rolesTableName,
          RRM_TABLE_NAME: rrmTableName,
        },
        {
          code: discoveryCode,
          handler: "discovery_tool_handler.handler",
          timeout: cdk.Duration.seconds(45),
        },
      );
      // Its own backend only (invoke its Lambda, or GetItem on its table), and
      // read-only on the two grant tables.
      alias.lambda.addToRolePolicy(
        "lambda" in backend
          ? new iam.PolicyStatement({
              actions: ["lambda:InvokeFunction"],
              resources: [backendEnv.BACKEND_LAMBDA_ARN],
            })
          : new iam.PolicyStatement({
              actions: ["dynamodb:GetItem"],
              resources: [tableArn(backendEnv.NAMESPACES_TABLE)],
            }),
      );
      alias.lambda.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ["dynamodb:GetItem", "dynamodb:Query"],
          resources: [
            tableArn(rolesTableName),
            tableArn(rrmTableName),
            `${tableArn(rrmTableName)}/index/*`,
          ],
        }),
      );
      this.toolAliases[tool] = alias;
      new cdk.CfnOutput(this, `ToolAliasArn${pascal(tool)}`, {
        value: alias.functionArn,
        description: `Agent tool '${tool}' :PROD alias (${backendName})`,
      });
    }

    // tool -> alias ARN, for the factory catalogue to copy.
    const toolAliasMap = cdk.Stack.of(this).toJsonString(
      Object.fromEntries(
        Object.entries(this.toolAliases).map(([t, a]) => [t, a.functionArn]),
      ),
    );
    new ssm.StringParameter(this, "ToolAliasArnsParam", {
      parameterName: `${ssmPrefix}/serve-tool/tool-alias-arns`,
      stringValue: toolAliasMap,
      description:
        "Agent tool -> :PROD alias ARN (JSON), for gateway Lambda targets",
    });
    new cdk.CfnOutput(this, "ToolAliasArns", {
      value: toolAliasMap,
      description: `Agent tool -> :PROD alias ARN; schemas in ${SERVE_TOOL_DIR}/tool-schemas.json and ${DISCOVERY_TOOL_DIR}/tool-schemas.json`,
    });
  }
}
