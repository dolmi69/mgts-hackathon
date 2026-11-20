import { apiGet, apiRequest } from "./api.js";

const endpoint = "/api/demo/feedback";
const form = document.getElementById("feedback-form");
const fields = form.querySelector("fieldset");
const score = document.getElementById("feedback-score");
const comment = document.getElementById("feedback-comment");
const status = document.getElementById("feedback-status");
const error = document.getElementById("feedback-error");
const card = document.getElementById("feedback-card");
const check = document.getElementById("feedback-check");

let draft = null;
let submissionId = new URLSearchParams(location.search).get("submission");

function showStatus(result) {
  card.href = `intern.html?id=${result.intern_id}#answer-${result.answer_id}`;
  card.hidden = false;
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
  }
}

comment.addEventListener("input", () => comment.setCustomValidity(""));
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = comment.value.trim();
  if (!text) {
    comment.setCustomValidity("Напишите комментарий.");
    comment.reportValidity();
    return;
  }
  const value = Number(score.value);
  if (!draft || draft.score !== value || draft.comment !== text) {
    draft = { submission_id: crypto.randomUUID(), score: value, comment: text };
  }
  status.textContent = "Отправляем анкету…";
  card.hidden = true;
  submissionId = null;

  await run(async () => {
    const result = await apiRequest(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    submissionId = result.submission_id;
    const url = new URL(location.href);
    url.searchParams.set("submission", submissionId);
    history.replaceState({}, "", url);
    draft = null;
    form.reset();
    if (!showStatus(result)) await pollStatus();
  });
});

check.addEventListener("click", () => run(pollStatus));
if (submissionId) run(pollStatus);
