import { beforeEach, afterEach } from "node:test";
import * as chai from "chai";
import { GLOBAL_EXPECT, JestChaiExpect, JestExtend, JestAsymmetricMatchers, getState, setState } from "@vitest/expect";
import { fn, spyOn, restoreAllMocks } from "@vitest/spy";
import * as matchers from "@testing-library/jest-dom/matchers";
import { cleanup } from "@testing-library/react";

chai.use(JestChaiExpect);
chai.use(JestExtend);
chai.use(JestAsymmetricMatchers);
export const expect = Object.assign((value: unknown, message?: string) => chai.expect(value, message), chai.expect);
globalThis[GLOBAL_EXPECT] = expect;
expect.getState = () => getState(expect);
expect.setState = (state: Record<string, unknown>) => setState(state, expect);
expect.extend = (extensions: Record<string, unknown>) => chai.expect.extend(expect, extensions);
setState({ assertionCalls: 0, isExpectingAssertions: false, isExpectingAssertionsError: null,
  expectedAssertionsNumber: null, expectedAssertionsNumberErrorGen: null, suppressedErrors: [],
  soft: false, poll: false, currentTestName: "component test" }, expect);
expect.extend(matchers);

export const spies = { fn, spyOn };
const responses = new Map<string, ReturnType<typeof fn>>();
export function apiMock(name: string) {
  const response = fn();
  responses.set(name, response);
  return response;
}

function endpoint(pathname: string, method: string, options: RequestInit, query: URLSearchParams): [string, unknown[]] {
  const segments = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  const params = Object.fromEntries([...query].map(([key, value]) => [key, ["page", "page_size"].includes(key) ? Number(value) : value]));
  const [, resource, identifier, action] = segments;
  if (resource === "tasks") {
    if (!identifier) return method === "POST" ? ["createTask", [options.body]] : ["listTasks", [params]];
    if (identifier === "summary") return ["getTaskSummary", []];
    if (!action) return ["getTask", [identifier]];
    if (action === "events") return ["listTaskEvents", query.has("cursor") ? [identifier, query.get("cursor")] : [identifier]];
    if (action === "weekly-report-versions") return ["listTaskWeeklyReportVersions", [identifier]];
    if (action === "confirm") return ["confirmTask", [identifier]];
    if (action === "feedback") return ["submitHumanFeedback", [identifier, options.body]];
    if (action === "supplements") return ["uploadTaskSupplement", [identifier, options.body]];
    if (["start", "pause", "resume", "cancel"].includes(action)) return ["controlTask", [identifier, action]];
  }
  if (resource === "artifacts") return method === "POST" ? ["uploadProjectArtifact", [options.body]] : ["listArtifacts", [params]];
  if (resource === "skills") {
    if (method === "PUT") return ["updateSkill", [identifier, JSON.parse(String(options.body))]];
    if (method === "DELETE") return ["deleteSkill", [identifier]];
    return method === "POST" ? ["createSkill", [options.body]] : ["listSkills", []];
  }
  if (resource === "traces") {
    if (identifier === "tasks") return ["getTaskTrace", [action]];
    if (action === "structured") return ["getStructuredTrace", [identifier]];
  }
  throw new Error(`Unhandled API request: ${method} ${pathname}`);
}

const originalFetch = globalThis.fetch;
beforeEach(() => {
  for (const response of responses.values()) response.mockReset();
  globalThis.fetch = async (input, options = {}) => {
    const url = new URL(String(input), "http://localhost");
    const [name, args] = endpoint(url.pathname, options.method ?? "GET", options, url.searchParams);
    const response = responses.get(name);
    if (!response) throw new Error(`No fixture for ${name}`);
    try {
      const payload = await response(...args);
      return payload === undefined ? new Response(null, { status: 204 }) : Response.json(payload);
    } catch (error) {
      return Response.json({ detail: error.message }, { status: error.status ?? 500 });
    }
  };
});
afterEach(() => {
  cleanup();
  restoreAllMocks();
  globalThis.fetch = originalFetch;
});
