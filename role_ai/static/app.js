"use strict";
const $ = (id) => document.getElementById(id);
let csrf = "", activeConversation = null, busy = false;
let noticeTimer;

function notify(message) {
  $("notice").textContent = message;
  $("notice").hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 8000);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    if (response.status === 401 && !path.includes("/auth/login")) showAuth();
    const detail = typeof data?.detail === "string" ? data.detail : "입력값을 확인해 주세요.";
    throw new Error(detail);
  }
  return data;
}

function showAuth() {
  csrf = ""; activeConversation = null;
  $("workspace").hidden = true; $("auth-view").hidden = false;
  $("messages").replaceChildren(); $("conversation-list").replaceChildren();
}

function setBusy(value) {
  busy = value;
  for (const id of ["send", "new-chat", "provider", "skill", "probe", "logout"]) {
    $(id).disabled = value;
  }
  for (const button of $("conversation-list").querySelectorAll("button")) button.disabled = value;
  $("work-state").textContent = value ? "답변 생성 중…" : "준비됨 · 도구 실행 비활성화";
}

function emptyState() {
  $("messages").replaceChildren();
  const container = document.createElement("div");
  container.className = "empty";
  const title = document.createElement("h2"); title.textContent = "어떤 생각으로 시작할까요?";
  const description = document.createElement("p"); description.textContent = "모델과 Skill을 선택하고 대화를 시작하세요.";
  container.append(title, description); $("messages").append(container);
}

function addMessage(message) {
  const element = document.createElement("article");
  element.className = "message " + (message.role === "user" ? "user" : "assistant");
  const meta = document.createElement("small");
  meta.textContent = message.role === "user" ? "나" :
    ["Role-AI", message.provider, message.model, message.skill].filter(Boolean).join(" · ");
  const text = document.createElement("div"); text.textContent = message.content;
  element.append(meta, text); $("messages").append(element);
}

async function loadConversations() {
  const conversations = await api("/api/conversations");
  $("conversation-list").replaceChildren();
  for (const conversation of conversations) {
    const button = document.createElement("button");
    button.textContent = conversation.title;
    button.classList.toggle("active", activeConversation?.id === conversation.id);
    button.disabled = busy;
    button.addEventListener("click", () => {
      if (busy) return;
      openConversation(conversation).catch((e) => notify(e.message));
    });
    $("conversation-list").append(button);
  }
}

async function openConversation(conversation) {
  const messages = await api("/api/conversations/" + conversation.id + "/messages");
  activeConversation = conversation; $("chat-title").textContent = conversation.title;
  $("messages").replaceChildren();
  if (!messages.length) emptyState();
  else messages.forEach(addMessage);
  await loadConversations();
  $("messages").scrollTop = $("messages").scrollHeight;
}

async function enterWorkspace(data) {
  csrf = data.csrf_token; $("account").textContent = data.user.email;
  $("auth-view").hidden = true; $("workspace").hidden = false;
  const status = await api("/api/status");
  $("provider").replaceChildren();
  for (const item of status.providers) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.id + " · " + (item.model || "모델 미설정") +
      (item.usable ? "" : " · 미구현") + (item.state === "simulated" ? " · 테스트 전용" : "");
    option.disabled = !item.usable; $("provider").append(option);
  }
  const usable = status.providers.find((p) => p.id === status.default_provider && p.usable)
    || status.providers.find((p) => p.usable);
  $("provider").value = usable?.id || "";
  $("skill").replaceChildren();
  for (const item of status.skills) {
    const option = document.createElement("option");
    option.value = item.id; option.textContent = item.label; $("skill").append(option);
  }
  updateProviderState(); emptyState(); await loadConversations();
}

function updateProviderState() {
  $("connection-state").textContent = $("provider").value === "mock"
    ? "테스트 모드입니다. 실제 AI나 외부 서비스가 호출되지 않습니다."
    : "Ollama 연결 미확인 · 연결 테스트로 서버와 모델을 확인하세요.";
}

async function authenticate(register = false) {
  const email = $("email").value, password = $("password").value;
  if (!$("auth-form").reportValidity()) return;
  $("login").disabled = true; $("register").disabled = true;
  try {
    const data = await api("/api/auth/" + (register ? "register" : "login"), {
      method: "POST", body: JSON.stringify({ email, password })
    });
    $("password").value = ""; await enterWorkspace(data);
  } catch (error) { notify(error.message); }
  finally { $("login").disabled = false; $("register").disabled = false; }
}

$("auth-form").addEventListener("submit", (event) => { event.preventDefault(); authenticate(); });
$("register").addEventListener("click", () => authenticate(true));
$("provider").addEventListener("change", updateProviderState);
$("skill").addEventListener("change", () => {
  $("engine-badge").textContent = $("skill").selectedOptions[0].textContent;
});
$("new-chat").addEventListener("click", () => {
  if (busy) return;
  activeConversation = null; $("chat-title").textContent = "새로운 대화";
  emptyState(); loadConversations().catch((e) => notify(e.message));
});
$("logout").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); showAuth(); }
  catch (error) { notify(error.message); }
});
$("probe").addEventListener("click", async () => {
  setBusy(true);
  try {
    const result = await api("/api/providers/" + $("provider").value + "/test", { method: "POST" });
    $("connection-state").textContent = result.state === "simulated"
      ? "테스트 Provider 준비됨 · 실제 AI 연결 아님" : "연결 확인됨 · " + result.model;
  } catch (error) { $("connection-state").textContent = error.message; notify(error.message); }
  finally { setBusy(false); }
});
$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = $("message").value.trim();
  if (!content || busy) return;
  setBusy(true);
  try {
    if (!activeConversation) activeConversation = await api("/api/conversations", {
      method: "POST", body: JSON.stringify({ title: content.slice(0, 60) })
    });
    const result = await api("/api/conversations/" + activeConversation.id + "/chat", {
      method: "POST", body: JSON.stringify({
        content, provider: $("provider").value, skill: $("skill").value
      })
    });
    $("message").value = ""; await openConversation(activeConversation);
    $("engine-badge").textContent = result.simulated ? "테스트 응답" : "기본 비서 · " + result.skill;
    $("connection-state").textContent = result.simulated
      ? "테스트 응답입니다. 실제 AI 연결 아님." : "응답 수신 · " + result.model;
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
});
async function start() {
  try {
    const policy = await api("/api/auth/policy");
    $("register").hidden = !policy.registration_enabled;
    const response = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (response.ok) await enterWorkspace(await response.json());
    else if (response.status !== 401) notify("서버 상태를 확인해 주세요.");
  } catch (error) { notify("서버에 연결하지 못했습니다. " + error.message); }
}
start();
