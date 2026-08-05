import http from "k6/http";
import { check, fail, sleep } from "k6";
import { Trend } from "k6/metrics";

const baseUrl = (__ENV.BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const runId = __ENV.RUN_ID || `${Date.now()}`;
const thinkTimeSeconds = Number(__ENV.THINK_TIME || 6);
const endToEndLatency = new Trend("agentic_rag_e2e_latency", true);

export const options = {
  noCookiesReset: true,
  scenarios: {
    phase1_smoke: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || 10),
      duration: __ENV.DURATION || "2m",
      gracefulStop: "15s",
    },
  },
  thresholds: {
    checks: ["rate>0.99"],
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<1500"],
    agentic_rag_e2e_latency: ["p(95)<8000"],
  },
};

let registered = false;
let conversationId = null;

function jsonHeaders(extra = {}) {
  return {
    headers: {
      "Content-Type": "application/json",
      "User-Agent": `agentic-rag-k6/${runId}/${__VU}`,
      ...extra,
    },
  };
}

function ensureAccount() {
  if (registered) return;
  const username = `load_${runId}_${__VU}`.slice(0, 32);
  const response = http.post(
    `${baseUrl}/api/v1/auth/register`,
    JSON.stringify({
      email: `${username}@example.com`,
      username,
      password: "load-test-password-2026",
    }),
    jsonHeaders(),
  );
  if (!check(response, { "account registered": (item) => item.status === 201 })) {
    fail(`registration failed with status ${response.status}`);
  }
  const conversation = http.post(
    `${baseUrl}/api/v1/conversations`,
    JSON.stringify({ title: "Phase 1 压测" }),
    jsonHeaders(),
  );
  if (!check(conversation, { "conversation created": (item) => item.status === 201 })) {
    fail(`conversation creation failed with status ${conversation.status}`);
  }
  conversationId = conversation.json("id");
  registered = true;
}

export default function () {
  ensureAccount();
  const startedAt = Date.now();
  const accepted = http.post(
    `${baseUrl}/api/v1/conversations/${conversationId}/messages`,
    JSON.stringify({ content: "新生报到需要准备哪些材料？" }),
    jsonHeaders({ "Idempotency-Key": `${runId}-${__VU}-${__ITER}` }),
  );
  if (!check(accepted, { "run accepted": (item) => item.status === 202 })) {
    console.error(
      `run acceptance failed: status=${accepted.status} body=${String(accepted.body).slice(0, 300)}`,
    );
    sleep(thinkTimeSeconds);
    return;
  }

  let completed = false;
  for (let attempt = 0; attempt < 32; attempt += 1) {
    sleep(0.25);
    const detail = http.get(
      `${baseUrl}/api/v1/conversations/${conversationId}`,
      jsonHeaders(),
    );
    if (detail.status !== 200) continue;
    const messages = detail.json("messages") || [];
    if (messages.some((message) => message.role === "assistant")) {
      completed = true;
      break;
    }
  }

  check(completed, { "assistant response persisted": (value) => value === true });
  if (completed) endToEndLatency.add(Date.now() - startedAt);
  // Keep each simulated student below the configured 10 questions/minute
  // while still maintaining the requested number of concurrent sessions.
  sleep(thinkTimeSeconds);
}
