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

export interface ServeToolStackProps extends cdk.StackProps {
  /**
   * Principals allowed to invoke the `:PROD` aliases, typically the AgentCore
   * Gateway service roles of the gateways that target these tools (they may be
   * in another account). Empty = no resource policy, so only same-account
   * principals with an identity-policy grant can invoke.
   */
  readonly invokerPrincipalArns?: string[];
  /**
   * Gateway id -> COA system identity, e.g.
   * `{"gw-abc123": {"userId": "beacon-agent", "groups": ["beacon-agents"]}}`.
   * Written to SSM and read by the agent Lambda. A gateway not in the map is
   * refused. Defaults to `{}` (every agent call refused until mapped).
   */
  readonly gatewayIdentities?: Record<
    string,
    { userId: string; groups: string[] }
  >;
}

/**
 * Gateway Lambda tools in front of the two Context Manager (serve) runtimes.
 *
 * - `serve-tool-agent` -> serve-agent (IAM). SigV4 with its own role, which may
 *   InvokeAgentRuntime on serve-agent only. COA identity comes from the calling
 *   gateway's id via the SSM map, never from tool arguments.
 * - `serve-tool-user` -> serve (JWT). Forwards the human caller's ID token,
 *   propagated by the gateway in {@link SERVE_TOOL_ID_TOKEN_HEADER}. Serve
 *   verifies it and takes groups from it, so this role needs no AgentCore IAM.
 *
 * Both publish a `PROD` alias (the factory catalogue requires alias-pinned
 * targets) and live in the same account as serve, so the Lambda -> serve hop
 * never crosses an account. Tool schemas: `tool-schemas.json` next to the
 * handler, one tool per gateway target.
 */
export class ServeToolStack extends SCLStack {
  public readonly agentAlias: lambda.Alias;
  public readonly userAlias: lambda.Alias;

  constructor(scope: Construct, id: string, props: ServeToolStackProps = {}) {
    super(scope, id, props);
    this.addComponentTag("serve-tool");

    const { ssmPrefix } = resolveContext(this.node);

    const serveAgentRuntimeArn = ssm.StringParameter.valueForStringParameter(
      this,
      `${ssmPrefix}/serve/agent-runtime-arn`,
    );
    const serveRuntimeArn = ssm.StringParameter.valueForStringParameter(
      this,
      `${ssmPrefix}/serve/runtime-arn`,
    );

    const gatewayIdentityParam = new ssm.StringParameter(
      this,
      "GatewayIdentityParam",
      {
        parameterName: `${ssmPrefix}/serve-tool/gateway-identities`,
        stringValue: JSON.stringify(props.gatewayIdentities ?? {}),
        description:
          "Gateway id -> COA system identity for the serve-tool-agent Lambda",
      },
    );

    // No pip deps: the handler uses only stdlib + botocore from the runtime.
    const code = bundlePython({
      srcDirs: [fromRoot(SERVE_TOOL_DIR)],
      architecture: "arm64",
    });

    const makeFn = (
      name: string,
      mode: "agent" | "user",
      runtimeArn: string,
      extraEnv: Record<string, string>,
    ): lambda.Alias => {
      const fn = new lambda.Function(this, `${name}Fn`, {
        functionName: this.prefixed(`serve-tool-${mode}`),
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.ARM_64,
        handler: "serve_tool_handler.handler",
        code,
        // Serve's own resolve budget is 170s; leave headroom for the hop.
        timeout: cdk.Duration.seconds(180),
        memorySize: 256,
        environment: {
          SERVE_TOOL_MODE: mode,
          SERVE_RUNTIME_ARN: runtimeArn,
          SERVE_INVOKE_TIMEOUT_S: "170",
          ...extraEnv,
        },
        description: `COA serve gateway tool (${mode}) -> ${mode === "agent" ? "serve-agent (IAM)" : "serve (JWT)"}`,
      });
      // currentVersion re-publishes when code or config changes, so PROD
      // always points at what this deploy shipped.
      const alias = new lambda.Alias(this, `${name}ProdAlias`, {
        aliasName: "PROD",
        version: fn.currentVersion,
      });
      for (const [i, arn] of (props.invokerPrincipalArns ?? []).entries()) {
        alias.addPermission(`${name}Invoker${i}`, {
          principal: new iam.ArnPrincipal(arn),
          action: "lambda:InvokeFunction",
        });
      }
      return alias;
    };

    this.agentAlias = makeFn("Agent", "agent", serveAgentRuntimeArn, {
      GATEWAY_IDENTITY_PARAM: gatewayIdentityParam.parameterName,
    });
    // Invoke serve-agent only: the bare runtime and its endpoints.
    this.agentAlias.lambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock-agentcore:InvokeAgentRuntime"],
        resources: [serveAgentRuntimeArn, `${serveAgentRuntimeArn}/*`],
      }),
    );
    gatewayIdentityParam.grantRead(this.agentAlias.lambda);

    this.userAlias = makeFn("User", "user", serveRuntimeArn, {
      ID_TOKEN_HEADER: SERVE_TOOL_ID_TOKEN_HEADER,
    });

    new ssm.StringParameter(this, "AgentAliasArnParam", {
      parameterName: `${ssmPrefix}/serve-tool/agent-alias-arn`,
      stringValue: this.agentAlias.functionArn,
      description: "serve-tool-agent :PROD alias ARN (gateway Lambda target)",
    });
    new ssm.StringParameter(this, "UserAliasArnParam", {
      parameterName: `${ssmPrefix}/serve-tool/user-alias-arn`,
      stringValue: this.userAlias.functionArn,
      description: "serve-tool-user :PROD alias ARN (gateway Lambda target)",
    });

    const toolNames = (
      JSON.parse(
        fs.readFileSync(fromRoot(`${SERVE_TOOL_DIR}/tool-schemas.json`), "utf8"),
      ) as { name: string }[]
    ).map((t) => t.name);

    new cdk.CfnOutput(this, "AgentAliasArn", {
      value: this.agentAlias.functionArn,
      description: "Agent tool :PROD alias (serve-agent, IAM)",
    });
    new cdk.CfnOutput(this, "UserAliasArn", {
      value: this.userAlias.functionArn,
      description: "User tool :PROD alias (serve, JWT passthrough)",
    });
    new cdk.CfnOutput(this, "ToolNames", {
      value: toolNames.join(","),
      description: `One gateway target per tool; schemas in ${SERVE_TOOL_DIR}/tool-schemas.json`,
    });
  }
}
