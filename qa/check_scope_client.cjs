"use strict";
// Exercise the actual approved client snippets, with synthetic values only.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("role_ai/static/app.js", "utf8");
const html = fs.readFileSync("role_ai/static/index.html", "utf8");
const field = { value: "" };
const calls = [];
const ctx = vm.createContext({
  $: id => { assert.equal(id, "project"); return field; },
  content: "Synthetic task", activeConversation: null,
  api: async (path, options) => {
    calls.push({ path, body: JSON.parse(options.body) });
    return { id: "synthetic-conversation" };
  },
});
const start = source.indexOf("function scopedURL(");
const end = source.indexOf("async function loadMemories", start);
assert.ok(start >= 0 && end > start);
vm.runInContext(source.slice(start, end), ctx);
for (const value of ["", " A ", "프로젝트", "A&project=B#fragment"]) {
  field.value = value;
  const url = new URL(ctx.scopedURL("/api/documents"), "http://synthetic.invalid");
  assert.equal(url.searchParams.get("project"), value);
  assert.equal([...url.searchParams].length, 1);
}
const createStart = source.indexOf('if (!activeConversation) activeConversation = await api("/api/conversations", {');
const createEnd = source.indexOf("const isPaid", createStart);
assert.ok(createStart >= 0 && createEnd > createStart);
vm.runInContext("async function createOnly() {" + source.slice(createStart, createEnd) + "}", ctx);
(async () => {
  field.value = "프로젝트 A";
  await ctx.createOnly();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/api/conversations");
  assert.equal(calls[0].body.project, field.value);
  assert.ok(source.includes('$("project").value = conversation.project'));
  assert.ok(source.includes('await api(scopedURL("/api/documents/" + record.id)'));
  assert.ok(source.includes('link.href = scopedURL("/api/documents/" + record.id + "/export")'));
  assert.ok(source.includes('$("memory-export").href = scopedURL("/api/memories/export")'));
  assert.ok(html.includes('id="memory-export"'));
  assert.ok(source.includes('actionButton("보관 처리"'));
  assert.ok(!source.includes('문서 원문과 검색용 발췌를 삭제할까요?'));
  assert.ok(!html.includes('placeholder="공통"'));
  console.log("Scope client: exact project forwarding, conversation creation and approved archival copy passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
