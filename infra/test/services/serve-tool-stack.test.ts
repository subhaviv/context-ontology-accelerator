// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Template, Match } from "aws-cdk-lib/assertions";
import { ServeToolStack } from "../../lib/stacks/services/serve-tool-stack";
import { DEFAULT_RESOURCE_PREFIX, DEFAULT_ENV } from "../../lib/constants";

jest.mock("../../lib/utils/python-bundling", () => ({
  bundlePython: () =>
    lambda.Code.fromInline("def handler(event, context): pass"),
}));

const TEST_CONTEXT = {
  resource_prefix: DEFAULT_RESOURCE_PREFIX,
  env: DEFAULT_ENV,
  "aws:cdk:bundling-stacks": [],
};

const GATEWAY_ROLE = "arn:aws:iam::111122223333:role/workstream-gateway-role";

function synth(props: ConstructorParameters<typeof ServeToolStack>[2] = {}) {
  const app = new cdk.App({ context: TEST_CONTEXT });
  const stack = new ServeToolStack(app, "TestServeTool", {
    env: { account: "123456789012", region: "us-east-1" },
    ...props,
  });
  return Template.fromStack(stack);
}

describe("ServeToolStack", () => {
  const template = synth({
    invokerPrincipalArns: [GATEWAY_ROLE],
    gatewayIdentities: {
      "gw-beacon": { userId: "beacon-agent", groups: ["beacon-agents"] },
    },
  });

  it("creates an agent and a user Lambda in their modes", () => {
    for (const mode of ["agent", "user"]) {
      template.hasResourceProperties("AWS::Lambda::Function", {
        Handler: "serve_tool_handler.handler",
        Environment: {
          Variables: Match.objectLike({ SERVE_TOOL_MODE: mode }),
        },
      });
    }
  });

  it("publishes a PROD alias on each function", () => {
    template.resourcePropertiesCountIs(
      "AWS::Lambda::Alias",
      { Name: "PROD" },
      2,
    );
  });

  it("grants InvokeAgentRuntime exactly once (agent Lambda, serve-agent only)", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    const invokeStatements = Object.values(policies).flatMap((p) =>
      (p.Properties.PolicyDocument.Statement as { Action: unknown }[]).filter(
        (s) =>
          ([] as unknown[])
            .concat(s.Action)
            .includes("bedrock-agentcore:InvokeAgentRuntime"),
      ),
    );
    expect(invokeStatements).toHaveLength(1);
    const stmt = JSON.stringify(invokeStatements[0]);
    expect(stmt).toContain("serveagentruntimearn");
    expect(stmt).not.toContain("serveruntimearn");
  });

  it("admits the configured gateway role on both PROD aliases", () => {
    template.resourcePropertiesCountIs(
      "AWS::Lambda::Permission",
      { Action: "lambda:InvokeFunction", Principal: GATEWAY_ROLE },
      2,
    );
  });

  it("writes the gateway identity map to SSM", () => {
    template.hasResourceProperties("AWS::SSM::Parameter", {
      Name: Match.stringLikeRegexp("/serve-tool/gateway-identities$"),
      Value: JSON.stringify({
        "gw-beacon": { userId: "beacon-agent", groups: ["beacon-agents"] },
      }),
    });
  });

  it("defaults to an empty map and no invoke permissions", () => {
    const bare = synth();
    bare.hasResourceProperties("AWS::SSM::Parameter", {
      Name: Match.stringLikeRegexp("/serve-tool/gateway-identities$"),
      Value: "{}",
    });
    bare.resourceCountIs("AWS::Lambda::Permission", 0);
  });
});
