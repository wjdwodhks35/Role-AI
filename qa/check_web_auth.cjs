"use strict";
// Exercise the real browser API functions without a browser, server, or user data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("role_ai/static/app.js", "utf8");
let authShown = 0, request;
const context = vm.createContext({
  csrf: "test-csrf", showAuth: () => { authShown++; },
  fetch: async (path, options) => { request = { path, options }; return context.response; },
});
vm.runInContext(source.slice(source.indexOf("async function api("), source.indexOf("function showAuth(")), context);

async function check() {
  const cases = [
    [401, "Unauthorized", "인증이 필요합니다"],
    [403, "<html>private proxy diagnostic</html>", "접근 권한"],
    [429, "rate limited", "요청이 많습니다"],
    [502, "bad gateway", "서버 응답"],
    [200, "<html>Access login</html>", "접속 주소"],
    [400, '{"detail":', "입력값"],
    [401, JSON.stringify({ detail: "이메일 또는 비밀번호를 확인해 주세요." }), "이메일 또는 비밀번호"],
    [422, JSON.stringify({ detail: [{ msg: "invalid" }] }), "입력값"],
  ];
  for (const [status, body, expected] of cases) {
    context.response = new Response(body, { status });
    await assert.rejects(context.api("/api/auth/login", { method: "POST", body: "{}" }), error => {
      assert.ok(error.message.includes(expected), error.message);
      assert.ok(!error.message.includes("Unexpected token"));
      assert.ok(!error.message.includes("private proxy diagnostic"));
      return true;
    });
  }
  assert.equal(authShown, 0, "A failed login must leave the login form available");
  context.response = new Response("Unauthorized", { status: 401 });
  await assert.rejects(context.api("/api/status"));
  assert.equal(authShown, 1, "An expired session must return to the login form");
  context.response = Response.json({ user: { id: "synthetic" }, csrf_token: "synthetic" });
  const result = await context.api("/api/auth/login", { method: "POST", body: "{}" });
  assert.equal(result.user.id, "synthetic");
  assert.equal(request.options.credentials, "same-origin");
  assert.equal(request.options.headers["X-CSRF-Token"], "test-csrf");
  context.response = new Response(null, { status: 204 });
  assert.equal(await context.api("/api/auth/logout", { method: "POST" }), null);
  const html = fs.readFileSync("role_ai/static/index.html", "utf8");
  const input = html.match(/<input id="password"[^>]*>/)[0];
  assert.ok(!input.includes("minlength="));
  assert.ok(input.includes('maxlength="128"') && input.includes("required"));
  console.log("Web authentication response and password input checks passed");
}
check().catch(error => { console.error(error); process.exitCode = 1; });
