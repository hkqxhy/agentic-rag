import http from "k6/http";
import { check, fail, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const baseUrl = (__ENV.BASE_URL || "http://127.0.0.1").replace(/\/$/, "");
const runId = __ENV.RUN_ID || `${Date.now()}`;
const modelE2ELatency = new Trend("agentic_rag_model_e2e_latency", true);
const modelGenerationSuccess = new Rate("agentic_rag_model_generation_success");
const groundedAnswer = new Rate("agentic_rag_model_grounded_answer");
const safetyFiltered = new Rate("agentic_rag_model_safety_filtered");

const questions = [
  "校园卡弄丢了怎么办？",
  "统一身份认证密码忘了怎么重置？",
];

export const options = {
  noCookiesReset: true,
  scenarios: {
    phase2_model: {
      executor: "per-vu-iterations",
      vus: Number(__ENV.VUS || 2),
      iterations: Number(__ENV.ITERATIONS || 2),
      maxDuration: __ENV.MAX_DURATION || "2m",
    },
  },
  thresholds: {
    checks: ["rate>0.99"],
    http_req_failed: ["rate<0.01"],
    agentic_rag_model_generation_success: ["rate>0.99"],
    agentic_rag_model_grounded_answer: ["rate>0.99"],
    agentic_rag_model_e2e_latency: [
      `p(95)<${Number(__ENV.E2E_P95_MS || 30000)}`,
    ],
  },
};

let registered = false;

function jsonHeaders(extra = {}) {
  return {
    headers: {
      "Content-Type": "application/json",
      "User-Agent": `agentic-rag-phase2-k6/${runId}/${__VU}`,
      ...extra,
    },
  };
}

function ensureAccount() {
  if (registered) return;
  const safeRunId = runId.replace(/[^A-Za-z0-9_]/g, "_");
  const vuSuffix = `_${__VU}`;
  const username = `${`model_${safeRunId}`.slice(0, 32 - vuSuffix.length)}${vuSuffix}`;
  const response = http.post(
    `${baseUrl}/api/v1/auth/register`,
    JSON.stringify({
      email: `${username}@example.com`,
      username,
      password: "model-load-password-2026",
    }),
    jsonHeaders(),
  );
  if (!check(response, { "account registered": (item) => item.status === 201 })) {
    fail(
      `registration failed with status ${response.status}: ${String(response.body).slice(0, 500)}`,
    );
  }
  registered = true;
}

export default function () {
  ensureAccount();
  const conversation = http.post(
    `${baseUrl}/api/v1/conversations`,
    JSON.stringify({ title: "Phase 2 模型压测" }),
    jsonHeaders(),
  );
  if (!check(conversation, { "conversation created": (item) => item.status === 201 })) {
    fail(`conversation creation failed with status ${conversation.status}`);
  }
  const conversationId = conversation.json("id");
  const question = questions[(__VU + __ITER) % questions.length];
  const startedAt = Date.now();
  const accepted = http.post(
    `${baseUrl}/api/v1/conversations/${conversationId}/messages`,
    JSON.stringify({ content: question }),
    jsonHeaders({ "Idempotency-Key": `${runId}-${__VU}-${__ITER}` }),
  );
  if (!check(accepted, { "run accepted": (item) => item.status === 202 })) {
    modelGenerationSuccess.add(false);
    return;
  }

  let assistant = null;
  for (let attempt = 0; attempt < Number(__ENV.POLL_ATTEMPTS || 90); attempt += 1) {
    sleep(1);
    const detail = http.get(
      `${baseUrl}/api/v1/conversations/${conversationId}`,
      jsonHeaders(),
    );
    if (detail.status !== 200) continue;
    const messages = detail.json("messages") || [];
    assistant = messages.find((message) => message.role === "assistant") || null;
    if (assistant) break;
  }

  const completed = check(assistant, {
    "assistant response persisted": (value) => value !== null,
  });
  if (completed) {
    const metadata = assistant.message_metadata || {};
    const agent = metadata.agent || {};
    const generation = (metadata.retrieval || {}).generation || {};
    const modelSucceeded = generation.mode === "llm";
    modelGenerationSuccess.add(modelSucceeded);
    groundedAnswer.add(agent.grounded === true);
    safetyFiltered.add(Boolean(generation.safety_filter));
    modelE2ELatency.add(Date.now() - startedAt);
    check(metadata, {
      "langgraph metadata persisted": () => agent.framework === "langgraph",
      "model answer accepted": () => modelSucceeded,
      "answer grounded": () => agent.grounded === true,
    });
  } else {
    modelGenerationSuccess.add(false);
    groundedAnswer.add(false);
  }

  http.del(`${baseUrl}/api/v1/conversations/${conversationId}`, null, jsonHeaders());
}
