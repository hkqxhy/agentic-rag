import http from "k6/http";
import { check, fail } from "k6";
import { Trend } from "k6/metrics";

const baseUrl = (__ENV.BASE_URL || "http://127.0.0.1").replace(/\/$/, "");
const runId = __ENV.RUN_ID || `${Date.now()}`;
const authLatency = new Trend("agentic_rag_platform_auth_latency", true);
const readLatency = new Trend("agentic_rag_platform_read_latency", true);
const writeLatency = new Trend("agentic_rag_platform_write_latency", true);
const journeyLatency = new Trend("agentic_rag_platform_journey_latency", true);

export const options = {
  noCookiesReset: true,
  scenarios: {
    platform_burst: {
      executor: "per-vu-iterations",
      vus: Number(__ENV.VUS || 20),
      iterations: Number(__ENV.ITERATIONS || 1),
      maxDuration: __ENV.MAX_DURATION || "2m",
    },
  },
  thresholds: {
    checks: ["rate>0.99"],
    http_req_failed: ["rate<0.01"],
    agentic_rag_platform_auth_latency: [
      `p(95)<${Number(__ENV.AUTH_P95_MS || 20000)}`,
    ],
    agentic_rag_platform_read_latency: [
      `p(95)<${Number(__ENV.READ_P95_MS || 3000)}`,
    ],
    agentic_rag_platform_write_latency: [
      `p(95)<${Number(__ENV.WRITE_P95_MS || 5000)}`,
    ],
    agentic_rag_platform_journey_latency: [
      `p(95)<${Number(__ENV.JOURNEY_P95_MS || 30000)}`,
    ],
  },
};

function requestOptions(operation) {
  return {
    headers: {
      "Content-Type": "application/json",
      "User-Agent": `agentic-rag-platform-k6/${runId}/${__VU}`,
    },
    tags: { operation },
  };
}

function requireStatus(response, expectedStatus, label) {
  const passed = check(response, {
    [label]: (item) => item.status === expectedStatus,
  });
  if (!passed) {
    fail(`${label}: status=${response.status} body=${String(response.body).slice(0, 500)}`);
  }
}

function safeUsername() {
  const normalized = runId.replace(/[^A-Za-z0-9_]/g, "_");
  const vuSuffix = `_${__VU}`;
  return `${`platform_${normalized}`.slice(0, 32 - vuSuffix.length)}${vuSuffix}`;
}

export default function () {
  const journeyStartedAt = Date.now();
  const username = safeUsername();
  const register = http.post(
    `${baseUrl}/api/v1/auth/register`,
    JSON.stringify({
      email: `${username}@example.com`,
      username,
      password: "platform-load-password-2026",
    }),
    requestOptions("register"),
  );
  authLatency.add(register.timings.duration);
  requireStatus(register, 201, "account registered");

  const homepage = http.get(`${baseUrl}/`, requestOptions("homepage"));
  readLatency.add(homepage.timings.duration);
  requireStatus(homepage, 200, "homepage loaded");

  const ready = http.get(`${baseUrl}/health/ready`, requestOptions("health_ready"));
  readLatency.add(ready.timings.duration);
  requireStatus(ready, 200, "service ready");

  const me = http.get(`${baseUrl}/api/v1/auth/me`, requestOptions("auth_me"));
  readLatency.add(me.timings.duration);
  requireStatus(me, 200, "session resolved");

  const initialList = http.get(
    `${baseUrl}/api/v1/conversations`,
    requestOptions("conversation_list"),
  );
  readLatency.add(initialList.timings.duration);
  requireStatus(initialList, 200, "conversation list loaded");

  const created = http.post(
    `${baseUrl}/api/v1/conversations`,
    JSON.stringify({ title: "Platform capacity verification" }),
    requestOptions("conversation_create"),
  );
  writeLatency.add(created.timings.duration);
  requireStatus(created, 201, "conversation created");
  const conversationId = created.json("id");

  const detail = http.get(
    `${baseUrl}/api/v1/conversations/${conversationId}`,
    requestOptions("conversation_detail"),
  );
  readLatency.add(detail.timings.duration);
  requireStatus(detail, 200, "conversation detail loaded");

  const renamed = http.patch(
    `${baseUrl}/api/v1/conversations/${conversationId}`,
    JSON.stringify({ title: "Platform capacity verified" }),
    requestOptions("conversation_rename"),
  );
  writeLatency.add(renamed.timings.duration);
  requireStatus(renamed, 200, "conversation renamed");

  const deleted = http.del(
    `${baseUrl}/api/v1/conversations/${conversationId}`,
    null,
    requestOptions("conversation_delete"),
  );
  writeLatency.add(deleted.timings.duration);
  requireStatus(deleted, 204, "conversation deleted");

  journeyLatency.add(Date.now() - journeyStartedAt);
}
