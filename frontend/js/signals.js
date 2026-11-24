import {
  apiGet, apiRequest, escapeHtml, formatDateTime, hideError, priorityBadge, showError,
  waitingBadge,
} from "./api.js";

const form = document.getElementById("signal-filters");
const filters = form.elements;
const list = document.getElementById("signals-items");
const error = document.getElementById("signals-error");
const status = document.getElementById("signals-loading");
const internFilter = document.getElementById("signal-intern-filter");
const scopeFilter = document.getElementById("scope-filter");
const pagination = document.getElementById("signals-pages");
const prev = document.getElementById("signals-prev");
const next = document.getElementById("signals-next");
const pageSize = 40;

const states = {
  new: "Новый",
  in_progress: "В работе",
  reviewed: "Разобран",
};

const followups = {
  waiting: "Не все нужные ответы обработаны",
  repeated: "Тот же сигнал появился снова",
  not_repeated: "Такого сигнала больше нет",
};

let internId = null;
let offset = 0;
let nextOffset = null;
let requestNumber = 0;

function actionForm(signal, canWrite) {
  if (!canWrite || signal.state === "reviewed" || (signal.state === "new" && !signal.active)) {
    return "";
  }
  const isNew = signal.state === "new";
  const assign = isNew ? `
    <label>Ответственный
      <input name="assigned_to" maxlength="80" placeholder="Имя или логин">
    </label>
  ` : "";
  return `
    <form class="signal-action" data-signal-id="${escapeHtml(signal.id)}"
      data-state="${isNew ? "in_progress" : "reviewed"}">
      ${assign}
      <label>Заметка
        <textarea name="note" rows="2" maxlength="2000" placeholder="${isNew
          ? "Что нужно проверить?"
          : "Что выяснили и о чём договорились?"}"></textarea>
      </label>
      <p class="alert" role="alert" hidden></p>
      <button class="button" type="submit">${isNew ? "Взять в работу" : "Разобрано"}</button>
    </form>
  `;
}

function actionHistory(history) {
  if (!history.length) return "";
  return `
    <details class="signal-inbox-history">
      <summary>История действий</summary>
      <ol>${history.map((action) => `
        <li>
          <strong>${escapeHtml(states[action.state])}</strong>
          <span class="muted">${escapeHtml(action.actor)} · ${escapeHtml(formatDateTime(action.created_at))}</span>
          ${action.note ? `<p class="signal-note">${escapeHtml(action.note)}</p>` : ""}
        </li>
      `).join("")}</ol>
    </details>
  `;
}

function renderSignal(signal, canWrite) {
  const card = `intern.html?id=${encodeURIComponent(signal.intern_id)}`;
  const followup = signal.next_survey
    ? `<p class="signal-inbox-followup">Следующая анкета · ${escapeHtml(signal.next_survey.period_label)}.
        ${escapeHtml(followups[signal.next_survey.outcome])}.</p>`
    : "";
  const sources = signal.evidence_answer_ids.map((answerId) =>
    `<a href="${card}#answer-${encodeURIComponent(answerId)}">Ответ №${escapeHtml(answerId)}</a>`
  ).join("");
  const stateClass = signal.state === "reviewed" ? "positive" : "neutral";
  return `
    <article class="card signal-inbox-item" data-severity="${escapeHtml(signal.severity)}">
      <header class="signal-inbox-header">
        <div class="chips">
          ${priorityBadge(signal.severity)}
          <span class="badge ${stateClass}">${escapeHtml(states[signal.state])}</span>
          ${waitingBadge(signal.waiting_days, signal.overdue)}
          ${signal.active ? "" : '<span class="badge neutral">Причина больше не подтверждается</span>'}
        </div>
        <span class="signal-inbox-subhead">${escapeHtml(signal.period_label)}</span>
      </header>
      <div class="signal-inbox-subhead">
        <a href="${card}">${escapeHtml(signal.intern_name)}</a>
        ${signal.direction ? ` · ${escapeHtml(signal.direction)}` : ""}
        ${signal.assigned_to ? ` · Ответственный — ${escapeHtml(signal.assigned_to)}` : ""}
      </div>
      <h2 class="signal-inbox-title">${escapeHtml(signal.title)}</h2>
      <p>${escapeHtml(signal.explanation)}</p>
      <div class="signal-inbox-links">
        ${sources || `<a href="${card}">Карточка стажёра</a>`}
        <small class="muted">правило ${escapeHtml(signal.rule_code)}</small>
      </div>
      ${followup}
      ${actionHistory(signal.history)}
      ${actionForm(signal, canWrite)}
    </article>
  `;
}

function queryParameters() {
  const query = new URLSearchParams();
  for (const key of ["state", "severity", "period"]) {
    if (filters[key].value) query.set(key, filters[key].value);
  }
  if (filters.scope.value === "demo") query.set("scope", "demo");
  if (internId) query.set("intern_id", internId);
  if (offset) query.set("offset", String(offset));
  return query;
}

async function loadSignals(message = "") {
  const request = ++requestNumber;
  const query = queryParameters();
  history.replaceState(null, "", query.size ? `?${query}` : location.pathname);
  hideError(error);
  status.textContent = "Загрузка сигналов…";
  internFilter.hidden = !internId;
  list.setAttribute("aria-busy", "true");
  pagination.hidden = true;
  try {
    const data = await apiGet(`/api/signals?${query}`);
    if (request !== requestNumber) return;
    scopeFilter.hidden = !data.demo_available;
    nextOffset = data.next_offset;
    pagination.hidden = offset === 0 && nextOffset === null;
    prev.disabled = offset === 0;
    next.disabled = nextOffset === null;
    if (data.items.length) {
      list.innerHTML = data.items.map((item) => renderSignal(item, data.can_write)).join("");
      status.textContent = message;
    } else {
      list.replaceChildren();
      status.textContent = message || "По этим фильтрам сигналов нет.";
    }
  } catch (problem) {
    if (request !== requestNumber) return;
    list.replaceChildren();
    status.textContent = "";
    showError(error, problem.message);
  } finally {
    if (request === requestNumber) list.removeAttribute("aria-busy");
  }
}

form.addEventListener("submit", (event) => event.preventDefault());
form.addEventListener("change", () => {
  offset = 0;
  loadSignals();
});
form.addEventListener("reset", (event) => {
  event.preventDefault();
  for (const key of ["state", "severity", "period"]) filters[key].value = "";
  filters.scope.value = "program";
  internId = null;
  offset = 0;
  loadSignals();
});
prev.addEventListener("click", () => {
  offset = Math.max(0, offset - pageSize);
  loadSignals();
});
next.addEventListener("click", () => {
  if (nextOffset === null) return;
  offset = nextOffset;
  loadSignals();
});

list.addEventListener("submit", async (event) => {
  event.preventDefault();
  const action = event.target;
  const button = action.querySelector("button");
  const actionError = action.querySelector(".alert");
  const values = new FormData(action);
  button.disabled = true;
  hideError(actionError);
  try {
    const updated = await apiRequest(`/api/signals/${encodeURIComponent(action.dataset.signalId)}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        state: action.dataset.state,
        assigned_to: values.get("assigned_to") || "",
        note: values.get("note") || "",
      }),
    });
    await loadSignals(updated.state === "reviewed" ? "Результат сохранён." : "Сигнал взят в работу.");
  } catch (problem) {
    showError(actionError, problem.message);
    button.disabled = false;
  }
});

const initial = new URLSearchParams(location.search);
for (const filter of ["state", "severity", "period", "scope"]) {
  if (initial.has(filter)) {
    const value = initial.get(filter);
    const field = filters[filter];
    if (field.type === "month" || Array.from(field.options).some((option) => option.value === value)) {
      field.value = value;
    }
  }
}
if (/^[1-9]\d*$/.test(initial.get("intern_id") || "")) internId = initial.get("intern_id");
const initialOffset = Number(initial.get("offset"));
if (Number.isSafeInteger(initialOffset) && initialOffset > 0) offset = initialOffset;
loadSignals();
