"use strict";
// Run the actual message input listeners with synthetic keyboard/IME events.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("role_ai/static/app.js", "utf8");
const listeners = {};
const input = {
  value: "안녕하세요",
  addEventListener: (name, listener) => { listeners[name] = listener; },
};
const send = { disabled: false };
let submitted = 0;
const form = { requestSubmit: button => { assert.equal(button, send); submitted++; } };
const context = vm.createContext({
  $: id => ({ message: input, send, "chat-form": form })[id], busy: false,
});
const start = source.indexOf("let messageComposing = false;");
const end = source.indexOf('$("project").addEventListener', start);
assert.ok(start >= 0 && end > start);
vm.runInContext(source.slice(start, end), context);

function key(overrides = {}) {
  const event = { key: "Enter", prevented: false,
    preventDefault() { this.prevented = true; }, ...overrides };
  const before = submitted;
  listeners.keydown(event);
  return { sent: submitted - before, prevented: event.prevented };
}
assert.deepEqual(key(), { sent: 1, prevented: true });
for (const modifier of ["shiftKey", "ctrlKey", "altKey", "metaKey"]) {
  assert.deepEqual(key({ [modifier]: true }), { sent: 0, prevented: false });
}
assert.deepEqual(key({ key: "a" }), { sent: 0, prevented: false });
for (const ime of [{ isComposing: true }, { keyCode: 229 }]) {
  assert.deepEqual(key(ime), { sent: 0, prevented: false });
}
listeners.compositionstart();
assert.deepEqual(key(), { sent: 0, prevented: false });
listeners.compositionend();
assert.deepEqual(key({ keyCode: 229 }), { sent: 0, prevented: false });
assert.deepEqual(key(), { sent: 1, prevented: true });
assert.deepEqual(key({ repeat: true }), { sent: 0, prevented: true });
context.busy = true;
assert.deepEqual(key(), { sent: 0, prevented: true });
context.busy = false;
send.disabled = true;
assert.deepEqual(key(), { sent: 0, prevented: true });
send.disabled = false;
input.value = " \n ";
assert.deepEqual(key(), { sent: 0, prevented: true });
input.value = "첫 줄\n둘째 줄";
assert.deepEqual(key(), { sent: 1, prevented: true });
assert.equal(input.value, "첫 줄\n둘째 줄");
console.log("Chat Enter/Shift+Enter, IME, repeat, busy and empty input checks passed");
