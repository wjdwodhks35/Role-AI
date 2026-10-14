"use strict";
const $ = (id) => document.getElementById(id);
let csrf = "", activeConversation = null, busy = false;
let noticeTimer;
const selectedDocuments = new Set();
let paidQuoteKey = null;
let editingMemory = null, reviewingDataset = null;

function resetPaidQuote() {
  paidQuoteKey = null;
  $("paid-approval").hidden = true;
  $("paid-consent").checked = false;
}

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
  resetPaidQuote();
  $("usage-history").replaceChildren();
  selectedDocuments.clear(); $("documents-dialog").close(); $("gpu-dialog").close();
  $("collection-list").replaceChildren(); $("auto-policy-history").replaceChildren();
  $("documents-list").replaceChildren(); $("documents-results").textContent = "";
  $("memory-dialog").close(); $("learning-dialog").close();
  $("workspace").hidden = true; $("auth-view").hidden = false;
  $("messages").replaceChildren(); $("conversation-list").replaceChildren();
}

function setBusy(value) {
  busy = value;
  for (const id of ["send", "new-chat", "engine", "provider", "skill", "probe", "logout"]) {
    $(id).disabled = value;
  }
  for (const button of $("conversation-list").querySelectorAll("button")) button.disabled = value;
  if (!value) $("provider").disabled = $("engine").value === "openclaw";
  $("work-state").textContent = value ? "답변 생성 중…" : "준비됨 · 참고 문서 " + selectedDocuments.size + "개 · 도구 실행 비활성화";
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
  element.append(meta, text);
  if (message.sources?.length) {
    const references = document.createElement("details");
    const label = document.createElement("summary");
    label.textContent = "검색된 참고 문서 · 답변 근거 자동 검증 아님";
    references.append(label);
    for (const source of message.sources) {
      references.append(actionButton(source.filename + " · 발췌 " + source.position, async () => {
        const record = await api("/api/documents/" + source.document_id);
        $("documents-results").textContent = record.content;
        $("documents-dialog").showModal();
      }));
    }
    element.append(references);
  }
  if (message.role === "user" && message.id) {
    const remember = document.createElement("button"); remember.textContent = "기억 후보 추출";
    remember.addEventListener("click", async () => {
      remember.disabled = true;
      try {
        await api("/api/memories/extract", { method: "POST",
          body: JSON.stringify({ source_message_id: message.id }) });
        await loadMemories(); $("memory-dialog").showModal();
      } catch (error) { notify(error.message); }
      finally { remember.disabled = false; }
    });
    element.append(remember);
  }
  $("messages").append(element);
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
  resetPaidQuote();
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
  $("retrieval-mode").value = "keyword";
  $("retrieval-mode").querySelector('[value="semantic"]').textContent = status.semantic_available ? "로컬 의미 검색 · 연결 미검증" : "로컬 의미 검색 · 모델 설정 필요";
  $("learning-open").hidden = !status.learning_available;
  $("gpu-open").hidden = !status.resource_settings_available;
  $("paid-status").textContent = status.paid_api_calls === "opt_in" ? "요청별 동의 필요" : "비활성화";
  $("usage-button").hidden = status.paid_api_calls !== "opt_in";
  resetPaidQuote();
  $("engine").value = "provider_fallback";
  $("provider").disabled = false;
  $("current-engine").textContent = "기본 비서";
  const gatewayOption = $("engine").querySelector('[value="openclaw"]');
  gatewayOption.disabled = !status.openclaw_available;
  gatewayOption.textContent = status.openclaw_available ? "OpenClaw · 연결 미확인" : "OpenClaw · 비활성화";
  $("gateway-status").textContent = status.openclaw_available ? "연결 미확인 · 테스트 필요" : "비활성화 · 로컬 소유자 전용";
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
  resetPaidQuote();
  if ($("engine").value === "openclaw") {
    $("connection-state").textContent = "OpenClaw 연결 미확인 · Gateway의 로컬 모델 사용";
    return;
  }
  if ($("provider").value === "openai") {
    $("connection-state").textContent = "유료 API · 보내기 전에 비용 확인과 동의가 필요합니다.";
    return;
  }
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
$("engine").addEventListener("change", () => {
  $("provider").disabled = $("engine").value === "openclaw";
  $("current-engine").textContent = $("engine").value === "openclaw" ? "OpenClaw" : "기본 비서";
  updateProviderState();
});
$("message").addEventListener("input", resetPaidQuote);
$("project").addEventListener("input", resetPaidQuote);
$("skill").addEventListener("change", () => {
  resetPaidQuote();
  $("engine-badge").textContent = $("skill").selectedOptions[0].textContent;
});
$("new-chat").addEventListener("click", () => {
  if (busy) return;
  resetPaidQuote();
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
    const path = $("engine").value === "openclaw" ? "/api/assistant/openclaw/test" : "/api/providers/" + $("provider").value + "/test";
    const result = await api(path, { method: "POST" });
    $("connection-state").textContent = result.state === "simulated"
      ? "테스트 Provider 준비됨 · 실제 AI 연결 아님" : "연결 확인됨 · " + (result.model || result.agent) + (result.engine ? " · 추론은 미검증" : "");
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
    const isPaid = $("engine").value === "provider_fallback" && $("provider").value === "openai";
    if (selectedDocuments.size && (isPaid || $("engine").value !== "provider_fallback")) {
      throw new Error("문서 대화는 기본 비서와 Ollama 또는 Mock을 선택하세요.");
    }
    const quoteKey = JSON.stringify([activeConversation.id, content, $("skill").value, $("project").value]);
    if (isPaid && paidQuoteKey !== quoteKey) {
      const quote = await api("/api/conversations/" + activeConversation.id + "/quote", {
        method: "POST", body: JSON.stringify({ content, project: $("project").value, provider: "openai", skill: $("skill").value })
      });
      $("paid-approval").hidden = false;
      $("paid-consent").checked = false;
      $("paid-quote").textContent = "예상 예약 비용 $" + quote.estimated_upper_usd +
        " / 요청 한도 $" + quote.request_budget_usd + " / UTC 일일 예산 $" + quote.daily_budget_usd +
        " / 사용·예약 $" + quote.used_or_reserved_usd +
        ". 실제 청구 금액을 보장하는 견적은 아닙니다.";
      if (quote.within_budget) {
        paidQuoteKey = quoteKey;
        notify("비용과 외부 전송을 확인하고 동의한 뒤 다시 보내세요.");
      } else {
        notify("설정한 예산이 부족하여 호출할 수 없습니다.");
      }
      return;
    }
    if (isPaid && !$("paid-consent").checked) {
      notify("이번 유료 API 호출에 동의해 주세요.");
      return;
    }
    const result = await api("/api/conversations/" + activeConversation.id + "/chat", {
      method: "POST", body: JSON.stringify({
        retrieval_mode: $("retrieval-mode").value, document_ids: [...selectedDocuments], content, project: $("project").value, allow_paid: isPaid && $("paid-consent").checked, engine: $("engine").value, provider: $("provider").value, skill: $("skill").value
      })
    });
    $("message").value = ""; await openConversation(activeConversation);
    $("engine-badge").textContent = result.simulated ? "테스트 응답" : (result.assistant_engine === "openclaw" ? "OpenClaw" : "기본 비서") + " · " + result.skill;
    $("connection-state").textContent = result.simulated
      ? "테스트 응답입니다. 실제 AI 연결 아님." : "응답 수신 · " + result.model;
  } catch (error) { notify(error.message); }
  finally { setBusy(false); }
});
$("usage-button").addEventListener("click", async () => {
  try {
    const result = await api("/api/usage");
    $("usage-history").replaceChildren();
    const heading = document.createElement("p");
    heading.textContent = "설정 단가로 계산한 USD 기록 · 실제 청구서 아님";
    $("usage-history").append(heading);
    for (const record of result.records.slice(0, 10)) {
      const line = document.createElement("p");
      line.textContent = record.model + " · " + record.state + " · $" + record.calculated_usd;
      $("usage-history").append(line);
    }
    if (!result.records.length) {
      const line = document.createElement("p"); line.textContent = "사용 기록이 없습니다.";
      $("usage-history").append(line);
    }
  } catch (error) { notify(error.message); }
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


function actionButton(label, handler) {
  const button = document.createElement("button"); button.textContent = label;
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try { await handler(); } catch (error) { notify(error.message); }
    finally { button.disabled = false; }
  });
  return button;
}
function clearMemoryForm() {
  editingMemory = null; $("memory-content").value = ""; $("memory-project").value = "";
}
async function loadMemories() {
  const records = await api("/api/memories"); $("memory-list").replaceChildren();
  for (const record of records) {
    const card = document.createElement("article"); card.className = "management-card";
    const title = document.createElement("strong");
    title.textContent = record.kind + " · " + (record.project || "공통") + " · " + record.state;
    const text = document.createElement("p"); text.textContent = record.content;
    const source = document.createElement("small");
    source.textContent = "출처: " + (record.source_message_id || "사용자 직접 등록") +
      (record.expires_at ? " · 만료 " + new Date(record.expires_at * 1000).toLocaleString() : "");
    card.append(title, text, source);
    if (record.state === "candidate") card.append(actionButton("기억 확인", async () => {
      await api("/api/memories/" + record.id + "/confirm", { method: "POST" }); await loadMemories();
    }));
    card.append(actionButton("수정", async () => {
      editingMemory = record; $("memory-content").value = record.content;
      $("memory-kind").value = record.kind; $("memory-project").value = record.project;
    }), actionButton("삭제", async () => {
      await api("/api/memories/" + record.id, { method: "DELETE" });
      if (editingMemory?.id === record.id) clearMemoryForm();
      await loadMemories();
    }));
    $("memory-list").append(card);
  }
}
$("memory-open").addEventListener("click", async () => {
  try { await loadMemories(); $("memory-dialog").showModal(); }
  catch (error) { notify(error.message); }
});
$("memory-close").addEventListener("click", () => $("memory-dialog").close());
$("memory-clear").addEventListener("click", clearMemoryForm);
$("memory-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = { content: $("memory-content").value, kind: $("memory-kind").value,
    project: $("memory-project").value };
  if (editingMemory) {
    body.revision = editingMemory.revision;
    body.source_message_id = editingMemory.source_message_id;
    body.expires_at = editingMemory.expires_at;
  }
  try {
    await api("/api/memories" + (editingMemory ? "/" + editingMemory.id : ""), {
      method: editingMemory ? "PATCH" : "POST", body: JSON.stringify(body)
    });
    clearMemoryForm(); await loadMemories(); notify("기억을 저장했습니다.");
  } catch (error) { notify(error.message); }
});
$("learning-open").addEventListener("click", async () => {
  try { await loadLearning(); $("learning-dialog").showModal(); }
  catch (error) { notify(error.message); }
});
$("learning-close").addEventListener("click", () => $("learning-dialog").close());
async function loadLearning() {
  await loadCollection();
  await loadAutoPolicy();
  const status = await api("/api/learning/status");
  $("learning-status").textContent = "SFT + QLoRA · GPU 실행 " +
    (status.training_enabled ? "활성화" : "비활성화") +
    " · HF 조회 " + (status.hf_download_enabled ? "활성화" : "비활성화") +
    " · 학습 모델은 자동 활성화되지 않습니다.";
  const datasets = await api("/api/learning/datasets");
  $("dataset-list").replaceChildren();
  for (const dataset of datasets) {
    const card = document.createElement("article"); card.className = "management-card";
    const title = document.createElement("strong"); title.textContent = dataset.name + " · " + dataset.state;
    const stats = document.createElement("p");
    stats.textContent = "학습 " + dataset.manifest.train_rows + " / 평가 " +
      dataset.manifest.eval_rows + " / 제외 " + dataset.manifest.excluded_rows +
      " · 라이선스 " + dataset.manifest.source.license;
    const reviews = document.createElement("pre"); reviews.textContent = JSON.stringify(dataset.reviews, null, 2);
    const link = document.createElement("a"); link.textContent = "데이터 카드 내보내기";
    link.href = "/api/learning/datasets/" + dataset.id + "/card"; link.download = "dataset-card.md";
    card.append(title, stats, link, reviews);
    if (dataset.state === "reviewed" && !dataset.approval_hash) {
      card.append(actionButton("자동 허용 정책으로 ChatGPT 심사", async () => {
        const result = await api("/api/learning/auto-approval/" + dataset.id + "/evaluate", { method: "POST" });
        notify("ChatGPT 자동 심사: " + result.state); await loadLearning();
      }));
    }
    const preview = document.createElement("pre");
    card.append(actionButton("정규화된 전체 선택 샘플 확인", async () => {
      const result = await api("/api/learning/datasets/" + dataset.id + "/samples");
      preview.textContent = JSON.stringify(result, null, 2);
    }), preview);
    card.append(actionButton("로컬 Teacher·Reviewer 검수", async () => {
      for (const stage of ["teacher", "reviewer"]) {
        await api("/api/learning/datasets/" + dataset.id + "/review", {
          method: "POST", body: JSON.stringify({ stage })
        });
      }
      await loadLearning();
    }), actionButton("GPT 검수 비용 확인", async () => {
      const quote = await api("/api/learning/datasets/" + dataset.id + "/review/quote", { method: "POST" });
      $("review-quote").textContent = dataset.name + " · 예상 예약 $" + quote.estimated_upper_usd +
        " / 요청 한도 $" + quote.request_budget_usd + " / 일일 예산 $" + quote.daily_budget_usd +
        ". 검수는 학습 미리보기 3개를 대상으로 하며 전체 품질 보장이 아닙니다.";
      $("review-consent").checked = false; $("review-approval").hidden = false;
      reviewingDataset = quote.within_budget ? dataset.id : null;
      if (!quote.within_budget) notify("예산이 부족합니다.");
    }));
    const checks = ["데이터 라이선스 조건 확인", "개인정보 및 샘플 품질 확인", "기반 모델 이용 조건 확인"];
    const inputs = [];
    for (const text of checks) {
      const label = document.createElement("label"); label.className = "muted";
      const check = document.createElement("input"); check.type = "checkbox";
      label.append(check, document.createTextNode(text)); card.append(label, document.createElement("br"));
      inputs.push(check);
    }
    card.append(actionButton("검수 결과 확인 후 학습 승인", async () => {
      if (!inputs.every(input => input.checked)) throw new Error("세 가지 검토 항목을 확인하세요.");
      await api("/api/learning/datasets/" + dataset.id + "/approve", {
        method: "POST", body: JSON.stringify({
          data_hash: dataset.data_hash, license_reviewed: true, privacy_reviewed: true,
          model_license_reviewed: true, max_steps: 30, max_length: 512
        })
      });
      await loadLearning();
    }), actionButton("승인된 GPU 작업 등록", async () => {
      await api("/api/learning/datasets/" + dataset.id + "/enqueue", { method: "POST" });
      await loadLearning();
    }), actionButton("데이터·종료된 작업 기록 삭제", async () => {
      await api("/api/learning/datasets/" + dataset.id, { method: "DELETE" }); await loadLearning();
    }));
    $("dataset-list").append(card);
  }
  const jobs = await api("/api/learning/jobs"); $("training-jobs").replaceChildren();
  for (const job of jobs) {
    const card = document.createElement("article"); card.className = "management-card";
    const text = document.createElement("pre"); text.textContent = job.id + " · " + job.state +
      "\n" + JSON.stringify(job.report, null, 2); card.append(text);
    if (job.state === "running") card.append(actionButton("Worker 중단 확인 후 상태 복구", async () => {
      await api("/api/learning/jobs/" + job.id + "/recover", { method: "POST" }); await loadLearning();
    }));
    if (job.state === "blocked") card.append(actionButton("자원 확보 후 재시도", async () => {
      await api("/api/learning/jobs/" + job.id + "/retry", { method: "POST" }); await loadLearning();
    }));
    if (["queued", "blocked"].includes(job.state)) card.append(actionButton("작업 취소", async () => {
      await api("/api/learning/jobs/" + job.id + "/cancel", { method: "POST" }); await loadLearning();
    }));
    $("training-jobs").append(card);
  }
}
$("dataset-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const file = $("dataset-file").files[0];
    if (!file || file.size > 48 * 1024) throw new Error("48KB 이하의 선별된 JSONL 파일을 선택하세요.");
    const samples = (await file.text()).split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
    if (samples.length < 10 || samples.length > 100) throw new Error("10~100행을 선택하세요.");
    await api("/api/learning/datasets", { method: "POST", body: JSON.stringify({
      name: $("dataset-name").value, license: $("dataset-license").value,
      provenance: $("dataset-provenance").value, samples
    }) });
    await loadLearning();
  } catch (error) { notify(error.message); }
});
$("hf-search").addEventListener("click", async () => {
  $("hf-search").disabled = true;
  try {
    const result = await api("/api/learning/huggingface/suggest", { method: "POST",
      body: JSON.stringify({ task: $("hf-query").value }) });
    $("hf-results").textContent = JSON.stringify(result, null, 2);
  } catch (error) { notify(error.message); }
  finally { $("hf-search").disabled = false; }
});
$("hf-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/learning/huggingface/import", { method: "POST", body: JSON.stringify({
      name: $("hf-repo").value, repo_id: $("hf-repo").value, revision: $("hf-revision").value,
      filename: $("hf-file").value, prompt_field: $("hf-prompt").value,
      completion_field: $("hf-completion").value, rows: 30
    }) });
    await loadLearning();
  } catch (error) { notify(error.message); }
});
$("review-send").addEventListener("click", async () => {
  if (!reviewingDataset || !$("review-consent").checked) {
    notify("비용 확인 후 이번 검수 호출에 동의하세요."); return;
  }
  $("review-send").disabled = true;
  try {
    await api("/api/learning/datasets/" + reviewingDataset + "/review", { method: "POST",
      body: JSON.stringify({ stage: "orchestrator", allow_paid: true }) });
    reviewingDataset = null; $("review-approval").hidden = true;
    await loadLearning();
  } catch (error) { notify(error.message); }
  finally { $("review-send").disabled = false; $("review-consent").checked = false; }
});


async function loadDocuments() {
  const records = await api("/api/documents");
  const available = new Set(records.map(record => record.id));
  for (const id of selectedDocuments) if (!available.has(id)) selectedDocuments.delete(id);
  $("documents-list").replaceChildren();
  for (const record of records) {
    const card = document.createElement("article"); card.className = "management-card";
    const label = document.createElement("label");
    const check = document.createElement("input"); check.type = "checkbox";
    check.checked = selectedDocuments.has(record.id);
    check.addEventListener("change", () => {
      if (check.checked && selectedDocuments.size >= 5) {
        check.checked = false; notify("문서는 최대 5개 선택하세요."); return;
      }
      if (check.checked) selectedDocuments.add(record.id);
      else selectedDocuments.delete(record.id);
      if (!busy) $("work-state").textContent = "준비됨 · 참고 문서 " + selectedDocuments.size + "개";
    });
    label.append(check, document.createTextNode(record.filename + " · " +
      (record.project || "공통") + " · " + record.characters + "자"));
    const link = document.createElement("a"); link.href = "/api/documents/" + record.id + "/export";
    link.textContent = record.filename.toLowerCase().endsWith(".pdf") ? "추출 텍스트 내려받기" : "원문 내려받기"; link.download = record.filename.toLowerCase().endsWith(".pdf") ? record.filename + ".txt" : record.filename;
    card.append(label, link, actionButton("삭제", async () => {
      if (!window.confirm("문서 원문과 검색용 발췌를 삭제할까요? 대화와 출처 식별자는 남습니다.")) return;
      await api("/api/documents/" + record.id, { method: "DELETE" });
      selectedDocuments.delete(record.id); $("documents-results").textContent = "";
      await loadDocuments();
    }));
    $("documents-list").append(card);
  }
}
$("documents-open").addEventListener("click", async () => {
  try { await loadDocuments(); $("documents-dialog").showModal(); }
  catch (error) { notify(error.message); }
});
$("documents-close").addEventListener("click", () => $("documents-dialog").close());
$("documents-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true;
  try {
    const file = $("documents-file").files[0];
    if (!file) throw new Error("파일을 선택하세요.");
    let result;
    if (file.name.toLowerCase().endsWith(".pdf")) {
      if (file.size > 512 * 1024) throw new Error("512KB 이하 PDF를 선택하세요.");
      const response = await fetch("/api/documents/pdf?" + new URLSearchParams({
        filename: file.name, project: $("documents-project").value
      }), { method: "POST", body: file, credentials: "same-origin",
        headers: { "Content-Type": "application/pdf", "X-CSRF-Token": csrf } });
      result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "PDF를 확인하세요.");
    } else {
      if (file.size > 48 * 1024) throw new Error("48KB 이하 텍스트 파일을 선택하세요.");
      const content = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      if (content.length > 16000) throw new Error("16,000자 이하 문서를 선택하세요.");
      result = await api("/api/documents", { method: "POST", body: JSON.stringify({
        filename: file.name, content, project: $("documents-project").value
      }) });
    }
    notify(result.duplicate ? "같은 프로젝트에 이미 저장된 문서입니다." : "문서를 보관했습니다.");
    $("documents-file").value = ""; await loadDocuments();
  } catch (error) { notify(error.message); }
  finally { button.disabled = false; }
});
$("documents-search").addEventListener("click", async () => {
  try {
    const result = $("retrieval-mode").value === "semantic"
      ? await api("/api/documents/semantic", { method: "POST", body: JSON.stringify({
          query: $("documents-query").value, project: $("project").value,
          document_ids: [...selectedDocuments]
        }) })
      : await api("/api/documents/search?" + new URLSearchParams({
          q: $("documents-query").value, project: $("project").value
        }));
    $("documents-results").textContent = result.sources.length
      ? result.sources.map(source => source.filename + " · 발췌 " + source.position +
        "\n" + source.text).join("\n\n") : "일치하는 근거가 없습니다.";
  } catch (error) { notify(error.message); }
});


async function loadCollection() {
  const plans = await api("/api/learning/collection");
  $("collection-list").replaceChildren();
  for (const plan of plans) {
    const card = document.createElement("article"); card.className = "management-card";
    const title = document.createElement("strong");
    title.textContent = plan.topic + " · " + plan.state + " · " +
      plan.runs + "/" + plan.max_runs + "회 · " + (plan.enabled ? "예약 활성" : "중지");
    const schedule = document.createElement("p");
    schedule.textContent = "Worker 실행 시 다음 수집: " + new Date(plan.next_run * 1000).toLocaleString();
    const history = document.createElement("pre"); history.textContent = JSON.stringify(plan.history, null, 2);
    card.append(title, schedule, history);
    if (plan.runs < plan.max_runs) card.append(actionButton(plan.enabled ? "예약 중지" : "예약 재개", async () => {
      await api("/api/learning/collection/" + plan.id + (plan.enabled ? "/pause" : "/resume"),
        { method: "POST" }); await loadCollection();
    }));
    if (plan.state !== "running") card.append(actionButton("계획 삭제", async () => {
      await api("/api/learning/collection/" + plan.id, { method: "DELETE" }); await loadCollection();
    }));
    $("collection-list").append(card);
  }
}
$("collection-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true;
  try {
    await api("/api/learning/collection", { method: "POST", body: JSON.stringify({
      topic: $("collection-topic").value, interval_hours: Number($("collection-hours").value),
      max_runs: Number($("collection-runs").value)
    }) });
    notify("자동 수집 계획을 등록했습니다. 실행 중인 Worker가 처리합니다.");
    await loadCollection();
  } catch (error) { notify(error.message); }
  finally { button.disabled = false; }
});
$("collection-refresh").addEventListener("click", async () => {
  try { await loadLearning(); }
  catch (error) { notify(error.message); }
});


async function loadAutoPolicy() {
  const policy = await api("/api/learning/auto-approval");
  $("auto-policy-status").textContent = "모드 " + (policy.enabled ? "켜짐" : "꺼짐") +
    " · 모델 " + (policy.model || "미설정") + " · 요청 예산 $" + policy.request_budget_usd +
    " · UTC 일일 전체 API 예산 $" + policy.daily_budget_usd +
    " · " + (policy.openai_configured ? "API 설정됨 · 실제 연결 별도 확인" : "API 설정 필요");
  const options = policy.options;
  $("auto-policy-enabled").checked = policy.enabled;
  $("auto-policy-paid").checked = !!options.allow_paid;
  $("auto-policy-license").checked = !!options.license_reviewed;
  $("auto-policy-privacy").checked = !!options.privacy_reviewed;
  $("auto-policy-model").checked = !!options.model_license_reviewed;
  $("auto-policy-skill").value = options.target_skill || "developer";
  $("auto-policy-steps").value = options.max_steps || 30;
  $("auto-policy-length").value = options.max_length || 512;
  const records = await api("/api/learning/auto-approval/history");
  $("auto-policy-history").replaceChildren();
  for (const record of records.slice(0, 20)) {
    const line = document.createElement("pre");
    line.textContent = record.dataset_id + " · " + record.state + " · " +
      new Date(record.created_at * 1000).toLocaleString() + "\n" +
      JSON.stringify(record.detail, null, 2);
    $("auto-policy-history").append(line);
  }
}
$("auto-policy-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true;
  try {
    await api("/api/learning/auto-approval", { method: "PUT", body: JSON.stringify({
      enabled: $("auto-policy-enabled").checked, allow_paid: $("auto-policy-paid").checked,
      license_reviewed: $("auto-policy-license").checked, privacy_reviewed: $("auto-policy-privacy").checked,
      model_license_reviewed: $("auto-policy-model").checked,
      target_skill: $("auto-policy-skill").value, max_steps: Number($("auto-policy-steps").value),
      max_length: Number($("auto-policy-length").value)
    }) });
    notify("자동 허용 정책을 저장했습니다."); await loadLearning();
  } catch (error) { notify(error.message); }
  finally { button.disabled = false; }
});


async function loadGpuSettings() {
  const data = await api("/api/settings/gpu-memory");
  const policy = data.policy, measurement = data.measurement;
  $("gpu-enabled").checked = policy.enabled;
  for (const [id, key] of [
    ["gpu-limit", "limit_mib"], ["gpu-reserve", "reserve_mib"],
    ["gpu-inference", "inference_mib"], ["gpu-embedding", "embedding_mib"],
    ["gpu-training", "training_mib"]
  ]) $(id).value = policy[key] / 1024;
  $("gpu-measurement").textContent = data.policy_state === "invalid"
    ? "저장된 GPU 설정이 손상됐습니다. 작업은 차단됩니다. 아래 값을 확인하고 다시 저장하세요."
    : measurement.state === "available"
    ? "GPU 0 · 전체 " + (measurement.total_mib / 1024).toFixed(2) +
      "GiB · 사용 " + (measurement.used_mib / 1024).toFixed(2) +
      "GiB · 유효 한도 " + (measurement.effective_limit_mib / 1024).toFixed(2) +
      "GiB · 검사 " + (policy.enabled ? "켜짐" : "꺼짐")
    : "GPU 사용량 미확인: " + measurement.reason;
}
$("gpu-open").addEventListener("click", async () => {
  try { await loadGpuSettings(); $("gpu-dialog").showModal(); }
  catch (error) { notify(error.message); }
});
$("gpu-close").addEventListener("click", () => $("gpu-dialog").close());
$("gpu-refresh").addEventListener("click", () => loadGpuSettings().catch(error => notify(error.message)));
$("gpu-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true;
  try {
    await api("/api/settings/gpu-memory", { method: "PUT", body: JSON.stringify({
      enabled: $("gpu-enabled").checked,
      limit_mib: Math.round(Number($("gpu-limit").value) * 1024),
      reserve_mib: Math.round(Number($("gpu-reserve").value) * 1024),
      inference_mib: Math.round(Number($("gpu-inference").value) * 1024),
      embedding_mib: Math.round(Number($("gpu-embedding").value) * 1024),
      training_mib: Math.round(Number($("gpu-training").value) * 1024)
    }) });
    notify("GPU 메모리 설정을 저장했습니다. 다음 작업부터 적용됩니다.");
    await loadGpuSettings();
  } catch (error) { notify(error.message); }
  finally { button.disabled = false; }
});
