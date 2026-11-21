import { apiGet, apiRequest } from "./api.js";

const endpoint = "/api/demo/feedback";
const form = document.getElementById("feedback-form");
const fields = form.querySelector("fieldset");
const role = document.getElementById("feedback-role");
const period = document.getElementById("feedback-period");
const score = document.getElementById("feedback-score");
const comment = document.getElementById("feedback-comment");
const status = document.getElementById("feedback-status");
const error = document.getElementById("feedback-error");
const card = document.getElementById("feedback-card");
const check = document.getElementById("feedback-check");
const inbox = document.getElementById("feedback-signals");
const scenario = document.getElementById("feedback-scenario");
const startOver = document.getElementById("feedback-new");
const example = document.getElementById("feedback-example");

let draft = null;
let scenarioId = null;
let submissionId = new URLSearchParams(location.search).get("submission");

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function rememberSubmission() {
  const url = new URL(location.href);
  if (submissionId) url.searchParams.set("submission", submissionId);
  else url.searchParams.delete("submission");
  history.replaceState({}, "", url);
}

function showStatus(result) {
  scenarioId = result.scenario_id;
  scenario.textContent = `Демо-стажёр №${result.intern_id}`;
  startOver.hidden = false;
  card.href = `intern.html?id=${result.intern_id}#answer-${result.answer_id}`;
  card.hidden = false;
  inbox.href = `signals.html?scope=demo&intern_id=${result.intern_id}`;
  inbox.hidden = !result.signal_codes.length;
  if (result.analysis_status === "done") {
    const signal = result.signal_codes.length
      ? `Сигналы: ${result.signal_codes.join(", ")}.`
      : "Флажков нет.";
    status.textContent = `Анализ готов (${result.model}). ${signal}`;
    return true;
  }
  if (result.analysis_status === "failed") {
    status.textContent = "Анкета сохранена. Модель не смогла разобрать комментарий.";
    return true;
  }
  status.textContent = "Анкета сохранена. ИИ разбирает комментарий…";
  return false;
}

async function pollStatus() {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const result = await apiGet(`${endpoint}/${encodeURIComponent(submissionId)}`);
    if (showStatus(result)) return;
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  status.textContent = "Анкета сохранена. Анализ ещё идёт.";
  check.hidden = false;
}

async function run(action) {
  fields.disabled = true;
  startOver.disabled = true;
  check.hidden = true;
  error.hidden = true;
  try {
    await action();
  } catch (problem) {
    status.textContent = "";
    error.textContent = problem.message || "Не удалось связаться с сервером.";
    error.hidden = false;
    check.hidden = !submissionId;
  } finally {
    fields.disabled = false;
    startOver.disabled = false;
  }
}

period.value = currentMonth();
example.addEventListener("click", () => {
  const mentor = role.value === "mentor";
  score.value = mentor ? "3" : "5";
  comment.value = mentor
    ? "Стажёр регулярно перерабатывает и не успевает закрывать задачи."
    : "Задач слишком много. Снова пришлось работать допоздна.";
  comment.setCustomValidity("");
});
startOver.addEventListener("click", () => {
  scenarioId = null;
  submissionId = null;
  draft = null;
  rememberSubmission();
  form.reset();
  period.value = currentMonth();
  scenario.textContent = "Новый сценарий";
  status.textContent = "";
  startOver.hidden = card.hidden = inbox.hidden = error.hidden = check.hidden = true;
});
comment.addEventListener("input", () => comment.setCustomValidity(""));
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = comment.value.trim();
  if (!text) {
    comment.setCustomValidity("Напишите комментарий.");
    comment.reportValidity();
    return;
  }
  const payload = {
    scenario_id: scenarioId,
    respondent_type: role.value,
    period: period.value,
    score: Number(score.value),
    comment: text,
  };
  if (!draft || Object.keys(payload).some((key) => payload[key] !== draft[key])) {
    draft = { submission_id: crypto.randomUUID(), ...payload };
  }
  status.textContent = "Отправляем анкету…";
  card.hidden = inbox.hidden = true;

  await run(async () => {
    const result = await apiRequest(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    submissionId = result.submission_id;
    rememberSubmission();
    draft = null;
    score.value = comment.value = "";
    if (result.respondent_type === "intern") role.value = "mentor";
    if (!showStatus(result)) await pollStatus();
  });
});

check.addEventListener("click", () => run(pollStatus));
if (submissionId) {
  run(async () => {
    const result = await apiGet(`${endpoint}/${encodeURIComponent(submissionId)}`);
    period.value = result.period.slice(0, 7);
    role.value = result.respondent_type === "intern" ? "mentor" : "intern";
    if (!showStatus(result)) await pollStatus();
  });
}
