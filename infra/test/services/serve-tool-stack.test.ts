// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Template, Match } from "aws-cdk-lib/assertions";
import {
  ServeToolStack,
  serveToolNames,
  discoveryToolNames,
} from "../../lib/stacks/services/serve-tool-stack";
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

const TOOLS = ["query", "translate_sparql", "rag_retrieval", "graph_traversal"];
const DISCOVERY = ["list_metrics", "describe_schema", "get_namespace"];
const ALL_TOOLS = [...TOOLS, ...DISCOVERY];
const GATEWAY_ROLE = "arn:aws:iam::111122223333:role/workstream-gateway-role";

function build(props: ConstructorParameters<typeof ServeToolStack>[2] = {}) {
  const app = new cdk.App({ context: TEST_CONTEXT });
  const stack = new ServeToolStack(app, "TestServeTool", {
    env: { account: "123456789012", region: "us-east-1" },
    ...props,
  });
  return { stack, template: Template.fromStack(stack) };
}

type Res = { Properties: Record<string, any> };

describe("ServeToolStack", () => {
  const { stack, template } = build();
  const functions = template.findResources("AWS::Lambda::Function") as Record<
    string,
    Res
  >;
  const agentFns = Object.entries(functions).filter(
    ([, f]) => f.Properties.Environment.Variables.SERVE_TOOL_NAME,
  );

  it("reads the 4 tools from tool-schemas.json", () => {
    expect(serveToolNames()).toEqual(TOOLS);
  });

  it("creates one agent function per tool, pinned by SERVE_TOOL_NAME", () => {
    expect(agentFns).toHaveLength(4);
    const names = agentFns.map(
      ([, f]) => f.Properties.Environment.Variables.SERVE_TOOL_NAME,
    );
    expect(names.sort()).toEqual([...TOOLS].sort());
    for (const [, f] of agentFns) {
      expect(f.Properties.FunctionName.length).toBeLessThanOrEqual(64);
      expect(f.Properties.FunctionName).not.toContain("_");
    }
  });

  it("runs every agent function as the fixed factory identity", () => {
    for (const [, f] of agentFns) {
      expect(f.Properties.Environment.Variables).toMatchObject({
        COA_AGENT_USER_ID: "factory-agent",
        COA_AGENT_GROUPS: "factory-agents",
      });
    }
  });

  it("has only the 7 M2M tool functions (no user/JWT function)", () => {
    expect(Object.keys(functions)).toHaveLength(7);
    const json = JSON.stringify(template.toJSON());
    for (const gone of [
      "serve-tool-user",
      "ID_TOKEN_HEADER",
      "SERVE_TOOL_MODE",
      "user-alias-arn",
    ]) {
      expect(json).not.toContain(gone);
    }
  });

  it("publishes 7 PROD aliases with unique ARNs, one per agent tool", () => {
    template.resourcePropertiesCountIs(
      "AWS::Lambda::Alias",
      { Name: "PROD" },
      7,
    );
    const arns = ALL_TOOLS.map((t) =>
      stack.resolve(stack.toolAliases[t].functionArn),
    );
    expect(new Set(arns.map((a) => JSON.stringify(a))).size).toBe(7);
  });

  it("scopes every agent role to InvokeAgentRuntime on serve-agent only", () => {
    const policies = template.findResources("AWS::IAM::Policy") as Record<
      string,
      Res
    >;
    const invoke = Object.values(policies).flatMap((p) =>
      (p.Properties.PolicyDocument.Statement as { Action: unknown }[]).filter(
        (s) =>
          ([] as unknown[])
            .concat(s.Action)
            .includes("bedrock-agentcore:InvokeAgentRuntime"),
      ),
    );
    expect(invoke).toHaveLength(4);
    for (const stmt of invoke) {
      const s = JSON.stringify(stmt);
      expect(s).toContain("serveagentruntimearn");
      expect(s).not.toContain("serveruntimearn");
    }
    // Each agent function's role carries exactly one such statement.
    const roleRefs = new Set(
      Object.values(policies)
        .filter((p) =>
          JSON.stringify(p.Properties.PolicyDocument).includes(
            "InvokeAgentRuntime",
          ),
        )
        .map((p) => JSON.stringify(p.Properties.Roles)),
    );
    expect(roleRefs.size).toBe(4);
  });

  it("has no gateway identity map", () => {
    const params = template.findResources("AWS::SSM::Parameter") as Record<
      string,
      Res
    >;
    for (const p of Object.values(params)) {
      expect(JSON.stringify(p.Properties.Name)).not.toContain(
        "gateway-identities",
      );
    }
    expect(JSON.stringify(template.toJSON())).not.toContain("GATEWAY_IDENTITY");
  });

  it("outputs each tool's alias ARN plus a tool -> ARN map", () => {
    const outputs = template.findOutputs("*");
    for (const id of [
      "ToolAliasArnQuery",
      "ToolAliasArnTranslateSparql",
      "ToolAliasArnRagRetrieval",
      "ToolAliasArnGraphTraversal",
      "ToolAliasArnListMetrics",
      "ToolAliasArnDescribeSchema",
      "ToolAliasArnGetNamespace",
      "ToolAliasArns",
    ]) {
      expect(outputs[id]).toBeDefined();
    }
    const map = JSON.stringify(outputs.ToolAliasArns.Value);
    for (const t of ALL_TOOLS) expect(map).toContain(t);
  });

  describe("discovery tools", () => {
    const discoveryFns = Object.values(functions).filter(
      (f) => f.Properties.Environment.Variables.DISCOVERY_TOOL_NAME,
    );
    const policies = Object.values(
      template.findResources("AWS::IAM::Policy") as Record<string, Res>,
    );

    it("creates one function per discovery tool with the fixed identity", () => {
      expect(discoveryToolNames()).toEqual(DISCOVERY);
      expect(discoveryFns).toHaveLength(3);
      for (const f of discoveryFns) {
        expect(f.Properties.Handler).toBe("discovery_tool_handler.handler");
        expect(f.Properties.Environment.Variables).toMatchObject({
          COA_AGENT_USER_ID: "factory-agent",
          COA_AGENT_GROUPS: "factory-agents",
          ROLES_TABLE_NAME: "coa-dev-roles",
          RRM_TABLE_NAME: "coa-dev-resource-role-mappings",
        });
        expect(f.Properties.Environment.Variables.SERVE_TOOL_NAME).toBe(
          undefined,
        );
      }
    });

    it("points each tool at its own backend Lambda", () => {
      const backends = Object.fromEntries(
        discoveryFns.map((f) => [
          f.Properties.Environment.Variables.DISCOVERY_TOOL_NAME,
          JSON.stringify(f.Properties.Environment.Variables.BACKEND_LAMBDA_ARN),
        ]),
      );
      expect(backends.list_metrics).toContain("coa-dev-metric-api");
      expect(backends.describe_schema).toContain("coa-dev-ontology-api-proxy");
      const getNs = discoveryFns.find(
        (f) =>
          f.Properties.Environment.Variables.DISCOVERY_TOOL_NAME ===
          "get_namespace",
      )!;
      expect(getNs.Properties.Environment.Variables).toMatchObject({
        NAMESPACES_TABLE: "coa-dev-namespaces",
      });
      expect(
        getNs.Properties.Environment.Variables.BACKEND_LAMBDA_ARN,
      ).toBeUndefined();
    });

    it("lets get_namespace only GetItem the namespaces table, invoke nothing", () => {
      const nsStmts = policies.flatMap((p) =>
        (
          p.Properties.PolicyDocument.Statement as {
            Action: unknown;
            Resource: unknown;
          }[]
        ).filter((s) =>
          JSON.stringify(s.Resource).includes("coa-dev-namespaces"),
        ),
      );
      expect(nsStmts).toHaveLength(1);
      expect(nsStmts[0].Action).toBe("dynamodb:GetItem");
      expect(([] as unknown[]).concat(nsStmts[0].Resource)).toHaveLength(1);
    });

    it("lets each role invoke only its own backend and read the grant tables", () => {
      const invokeStmts = policies.flatMap((p) =>
        (
          p.Properties.PolicyDocument.Statement as {
            Action: unknown;
            Resource: unknown;
          }[]
        ).filter((s) =>
          ([] as unknown[]).concat(s.Action).includes("lambda:InvokeFunction"),
        ),
      );
      expect(invokeStmts).toHaveLength(2);
      for (const s of invokeStmts) {
        expect(([] as unknown[]).concat(s.Resource)).toHaveLength(1);
      }
      const ddb = JSON.stringify(
        policies.flatMap((p) =>
          (
            p.Properties.PolicyDocument.Statement as { Action: unknown }[]
          ).filter((s) => JSON.stringify(s.Action).includes("dynamodb:")),
        ),
      );
      expect(ddb).toContain("coa-dev-roles");
      expect(ddb).toContain("coa-dev-resource-role-mappings");
      expect(ddb).not.toMatch(/PutItem|UpdateItem|DeleteItem|Scan/);
    });

    it("gives discovery roles no AgentCore access", () => {
      const discoveryPolicies = policies.filter((p) =>
        JSON.stringify(p.Properties.PolicyDocument).includes(
          "lambda:InvokeFunction",
        ),
      );
      for (const p of discoveryPolicies) {
        expect(JSON.stringify(p.Properties.PolicyDocument)).not.toContain(
          "bedrock-agentcore",
        );
      }
    });
  });

  it("adds no invoke permissions by default", () => {
    template.resourceCountIs("AWS::Lambda::Permission", 0);
  });

  it("admits configured invokers on every PROD alias", () => {
    const { template: t } = build({ invokerPrincipalArns: [GATEWAY_ROLE] });
    t.resourcePropertiesCountIs(
      "AWS::Lambda::Permission",
      { Action: "lambda:InvokeFunction", Principal: GATEWAY_ROLE },
      7,
    );
  });

  it("honours an overridden identity", () => {
    const { template: t } = build({
      agentUserId: "other",
      agentGroups: ["g1", "g2"],
    });
    t.hasResourceProperties("AWS::Lambda::Function", {
      Environment: {
        Variables: Match.objectLike({
          COA_AGENT_USER_ID: "other",
          COA_AGENT_GROUPS: "g1,g2",
        }),
      },
    });
  });
});
