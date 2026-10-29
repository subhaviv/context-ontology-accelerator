// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as fs from "fs";
import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { Construct } from "constructs";
import { resolveContext } from "../../context";
import { fromRoot } from "../../paths";
import { SCLStack } from "../../constructs";
import { bundlePython } from "../../utils/python-bundling";

const SERVE_TOOL_DIR = "packages/context-manager/lambda/serve-tool";

/** Header the gateway propagates the human caller's ID token in. */
export const SERVE_TOOL_ID_TOKEN_HEADER =
  "X-Amzn-Bedrock-AgentCore-Runtime-Custom-IdToken";

/** Default COA identity every agent-tool call runs as. */
export const DEFAULT_AGENT_USER_ID = "factory-agent";
export const DEFAULT_AGENT_GROUPS = ["factory-agents"];

/** Tool names from `tool-schemas.json`, in file order. */
export function serveToolNames(): string[] {
  return (
    JSON.parse(
      fs.readFileSync(fromRoot(`${SERVE_TOOL_DIR}/tool-schemas.json`), "utf8"),
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
 * Lambda tools in front of the two Context Manager (serve) runtimes.
 *
 * - Agent tools: ONE function per tool in `tool-schemas.json`
 *   (`serve-tool-<tool>`), each with its own `PROD` alias so every tool has a
 *   unique target ARN (the factory catalogue rejects two tools sharing one).
 *   Each calls serve-agent (IAM) with SigV4 and may InvokeAgentRuntime on
 *   serve-agent only. Every call runs as one fixed COA identity from props;
 *   admission is the calling platform's job, serve-agent's grants authorize the
 *   identity per namespace.
 * - `serve-tool-user` -> serve (JWT): one function for all tools, forwarding
 *   the human caller's ID token from {@link SERVE_TOOL_ID_TOKEN_HEADER}. Serve
 *   verifies it and takes groups from it, so this role needs no AgentCore IAM.
 *
 * Everything lives in the same account as serve, so the Lambda -> serve hop
 * never crosses an account.
 */
export class ServeToolStack extends SCLStack {
  /** Agent-tool `PROD` aliases, keyed by tool name. */
  public readonly toolAliases: Record<string, lambda.Alias> = {};
  public readonly userAlias: lambda.Alias;

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
    const serveRuntimeArn = ssm.StringParameter.valueForStringParameter(
      this,
      `${ssmPrefix}/serve/runtime-arn`,
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
    ): lambda.Alias => {
      const fn = new lambda.Function(this, `${idBase}Fn`, {
        functionName: this.prefixed(functionSuffix),
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.ARM_64,
        handler: "serve_tool_handler.handler",
        code,
        // Serve's own resolve budget is 170s; leave headroom for the hop.
        timeout: cdk.Duration.seconds(180),
        memorySize: 256,
        environment: { SERVE_INVOKE_TIMEOUT_S: "170", ...environment },
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
          SERVE_TOOL_MODE: "agent",
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

    this.userAlias = makeFn(
      "User",
      "serve-tool-user",
      "COA serve gateway tool (user) -> serve (JWT)",
      {
        SERVE_TOOL_MODE: "user",
        SERVE_RUNTIME_ARN: serveRuntimeArn,
        ID_TOKEN_HEADER: SERVE_TOOL_ID_TOKEN_HEADER,
      },
    );

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
    new ssm.StringParameter(this, "UserAliasArnParam", {
      parameterName: `${ssmPrefix}/serve-tool/user-alias-arn`,
      stringValue: this.userAlias.functionArn,
      description: "serve-tool-user :PROD alias ARN (gateway Lambda target)",
    });

    new cdk.CfnOutput(this, "ToolAliasArns", {
      value: toolAliasMap,
      description: `Agent tool -> :PROD alias ARN; schemas in ${SERVE_TOOL_DIR}/tool-schemas.json`,
    });
    new cdk.CfnOutput(this, "UserAliasArn", {
      value: this.userAlias.functionArn,
      description: "User tool :PROD alias (serve, JWT passthrough)",
    });
  }
}
