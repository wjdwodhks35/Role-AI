"use strict";
(() => {
  let timer, loading = false, selected = null, epoch = 0;
  const dialog = document.getElementById("coding-dialog");
  const element = (id) => document.getElementById(id);
  async function detail(id) {
    const requestEpoch = epoch;
    selected = id;
    const value = await api("/api/coding/tasks/" + encodeURIComponent(id));
    if (epoch !== requestEpoch) return;
    element("coding-result").textContent = JSON.stringify(value, null, 2);
  }
  async function refresh() {
    if (loading || !dialog.open) return;
    loading = true;
    const requestEpoch = epoch;
    try {
      const result = await api("/api/coding/tasks");
      if (epoch !== requestEpoch || !dialog.open) return;
      const list = element("coding-tasks"); list.replaceChildren();
      for (const task of result.tasks) {
        const row = document.createElement("div");
        const title = document.createElement("span");
        title.textContent = task.id + " · " + task.status + " / " + task.phase + " · 단계 " + task.steps + " · 토큰 " + task.tokens;
        row.append(title);
        for (const [label, action] of [["결과", "detail"], ["일시중지", "pause"], ["재개", "resume"], ["취소", "cancel"]]) {
          const button = document.createElement("button"); button.type = "button"; button.textContent = label;
          button.addEventListener("click", async () => {
            button.disabled = true;
            try {
              if (action === "detail") await detail(task.id);
              else {
                await api("/api/coding/tasks/" + task.id + "/" + action, {method:"POST", body:JSON.stringify({
                  approved: action === "resume", acknowledge_unknown: element("coding-unknown").checked,
                })});
                element("coding-unknown").checked = false;
                await detail(task.id);
              }
            } catch (error) { notify(error.message); }
            finally { button.disabled = false; }
          });
          row.append(button);
        }
        list.append(row);
      }
      if (selected) await detail(selected);
    } catch (error) { notify(error.message); clearInterval(timer); }
    finally { loading = false; }
  }
  element("coding-open").addEventListener("click", async () => {
    const requestEpoch = epoch;
    try {
      const status = await api("/api/coding/status");
      if (epoch !== requestEpoch) return;
      const select = element("coding-repository"); select.replaceChildren();
      for (const repository of status.repositories) {
        const option = document.createElement("option"); option.value = repository; option.textContent = repository;
        select.append(option);
      }
      dialog.showModal(); await refresh();
      clearInterval(timer); timer = setInterval(refresh, 3000);
    } catch (error) { notify(error.message); }
  });
  element("coding-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => clearInterval(timer));
  element("coding-refresh").addEventListener("click", refresh);
  window.addEventListener("roleai-logout", () => {
    epoch++;
    clearInterval(timer); selected = null; dialog.close();
    element("coding-result").textContent = ""; element("coding-tasks").replaceChildren();
    element("coding-form").reset(); element("coding-unknown").checked = false;
  });
  element("coding-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter; button.disabled = true;
    const number = (id) => Number(element(id).value);
    try {
      const task = await api("/api/coding/tasks", {method:"POST", body:JSON.stringify({
        repository_id: element("coding-repository").value, project_id: element("coding-project").value,
        request: element("coding-request").value,
        allowed_files: element("coding-files").value.split(/\r?\n/).map(p => p.trim()).filter(Boolean),
        command: element("coding-command").value, approved: element("coding-consent").checked,
        record_strategy: element("coding-record").checked,
        budget: {max_steps:number("coding-steps"), max_retries:number("coding-retries"),
          same_error_limit:number("coding-errors"), active_seconds:number("coding-seconds"),
          command_seconds:number("coding-timeout"), model_calls:number("coding-calls"),
          token_budget:number("coding-tokens"), memory_mib:number("coding-memory")},
      })});
      element("coding-consent").checked = false;
      await detail(task.id); await refresh(); notify("작업을 등록했습니다. 실행은 별도 Coding Worker가 담당합니다.");
    } catch (error) { notify(error.message); }
    finally { button.disabled = false; }
  });
})();
