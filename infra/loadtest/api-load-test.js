/**
 * k6 load test for ConstructAI API
 * Run: k6 run --env BASE_URL=http://localhost:8000 api-load-test.js
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

const errorRate = new Rate("errors");
const loginDuration = new Trend("login_duration");

export const options = {
  stages: [
    { duration: "30s", target: 10 }, // Ramp up
    { duration: "1m", target: 50 },  // Sustained load
    { duration: "30s", target: 100 }, // Peak
    { duration: "30s", target: 0 },  // Ramp down
  ],
  thresholds: {
    http_req_duration: ["p(95)<500", "p(99)<1000"],
    errors: ["rate<0.05"],
    http_req_failed: ["rate<0.05"],
  },
};

export default function () {
  // Health check
  const health = http.get(`${BASE_URL}/api/v1/health`);
  check(health, {
    "health status 200": (r) => r.status === 200,
  });
  errorRate.add(health.status !== 200);

  // Login attempt
  const loginStart = Date.now();
  const loginRes = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({
      email: `loadtest-${__VU}@example.com`,
      password: "LoadTest123!@#",
    }),
    { headers: { "Content-Type": "application/json" } }
  );
  loginDuration.add(Date.now() - loginStart);

  // Expect 401 (invalid creds) — we're testing throughput not auth
  check(loginRes, {
    "login responds": (r) => r.status === 401 || r.status === 200,
  });

  // Projects list (unauthenticated — expect 401/403)
  const projectsRes = http.get(`${BASE_URL}/api/v1/projects/`);
  check(projectsRes, {
    "projects responds": (r) => r.status < 500,
  });

  sleep(0.5);
}
