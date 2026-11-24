import { apiGet, escapeHtml, hideError, showError, topicLabel, TOPIC_LABELS } from "./api.js";

const periodSelect = document.getElementById("program-period");
const monthsSelect = document.getElementById("program-months");
const topicSelect = document.getElementById("program-topic");
const content = document.getElementById("program-content");
const status = document.getElementById("program-status");
const error = document.getElementById("program-error");
const dateFormat = new Intl.DateTimeFormat("ru-RU", { dateStyle: "long", timeZone: "UTC" });
const phases = { upcoming: "Опрос впереди", open: "Приём открыт", closed: "Срок прошёл" };
let map = null;
let requestId = 0;

topicSelect.add(new Option("Все темы", ""));
Object.entries(TOPIC_LABELS).forEach(([key, name]) => topicSelect.add(new Option(name, key)));

function renderCalendar(calendar) {
  document.getElementById("calendar-deadline").textContent =
    `Срок до ${dateFormat.format(new Date(calendar.deadline))} включительно`;
  document.getElementById("calendar-phase").textContent = phases[calendar.phase];
  const { expected, intern_responded, mentor_responded, intern_missing, mentor_missing } = calendar.totals;
  const metrics = [
    ["Ждём от каждой роли", expected],
    ["Ответили стажёры", intern_responded],
    ["Ответили наставники", mentor_responded],
    ["Нет ответа стажёра", intern_missing],
    ["Нет ответа наставника", mentor_missing],
  ];
  document.getElementById("calendar-totals").innerHTML = metrics.map(([label, count]) =>
    `<div><dt>${label}</dt><dd>${escapeHtml(count)}</dd></div>`
  ).join("");
  document.getElementById("calendar-directions").innerHTML = calendar.directions.map((row) => `
    <tr>
      <th scope="row">${escapeHtml(row.direction)}</th>
      <td>${escapeHtml(row.expected)}</td>
      <td>${escapeHtml(row.intern_responded)}</td>
      <td>${escapeHtml(row.mentor_responded)}</td>
      <td>${escapeHtml(row.intern_missing)}</td>
      <td>${escapeHtml(row.mentor_missing)}</td>
    </tr>`
  ).join("") || '<tr><td colspan="6">На этот месяц нет запланированных анкет.</td></tr>';
  document.getElementById("calendar-reminder").hidden = !calendar.reminder_draft;
  document.getElementById("calendar-draft").textContent = calendar.reminder_draft || "";
}

function topicCell(cell, selectedTopic) {
  if (!cell || cell.suppressed) return '<td class="muted">Мало данных</td>';
  const topics = selectedTopic ? cell.topics.filter(({ topic }) => topic === selectedTopic) : cell.topics;
  const largest = Math.max(0, ...topics.map(({ count }) => count));
  const intensity = largest === 0 ? 0 : Math.min(3, Math.ceil(3 * largest / cell.responded));
  const lines = topics.map(({ topic, count }) =>
    `<p>${escapeHtml(topicLabel(topic))}: <strong>${escapeHtml(count)}</strong> из ${escapeHtml(cell.responded)}</p>`
  ).join("") || '<p class="muted">По этой выборке жалоб нет</p>';
  return `<td data-intensity="${intensity}">${lines}</td>`;
}

function renderTopics() {
  if (!map) return;
  document.getElementById("program-privacy").textContent =
    `Считаем стажёров, отметивших тему. Показываем группы от ${map.min_group_size} разобранных ответов.`;
  document.getElementById("program-topics-head").innerHTML = `
    <tr><th scope="col">Направление</th>${map.periods.map(({ label }) =>
      `<th scope="col">${escapeHtml(label)}</th>`
    ).join("")}</tr>`;
  document.getElementById("program-topics-body").innerHTML = map.directions.map((row) => {
    const cells = new Map(row.periods.map((cell) => [cell.period, cell]));
    return `<tr><th scope="row">${escapeHtml(row.direction)}</th>${map.periods.map(({ period }) =>
      topicCell(cells.get(period), topicSelect.value)
    ).join("")}</tr>`;
  }).join("");
}

async function load() {
  const current = ++requestId;
  const period = periodSelect.value;
  const months = monthsSelect.value;
  hideError(error);
  status.textContent = "Загружаем данные…";
  const params = new URLSearchParams({ period });
  try {
    const [calendar, topics] = await Promise.all([
      apiGet(`/api/dashboard/calendar?${params}`),
      apiGet(`/api/dashboard/topics?${params}&months=${months}`),
    ]);
    if (requestId !== current) return;
    map = topics;
    renderCalendar(calendar);
    renderTopics();
    status.textContent = "";
    content.hidden = false;
    const url = new URL(location.href);
    url.searchParams.set("period", period);
    url.searchParams.set("months", months);
    history.replaceState({}, "", url);
  } catch (problem) {
    if (requestId !== current) return;
    status.textContent = "";
    content.hidden = true;
    showError(error, problem.message);
  }
}

async function init() {
  try {
    const periods = await apiGet("/api/periods");
    if (!periods.length) {
      status.textContent = "Нет запланированных опросов. Сначала загрузите справочник стажёров с датами начала.";
      return;
    }
    const requested = new URLSearchParams(location.search);
    periods.forEach(({ label, period }) => periodSelect.add(new Option(label, period)));
    // Months come from the survey schedule: open the last one that has answers.
    const selected = periods.find(({ period }) => period === requested.get("period"))
      || periods.find((item) => item.has_answers)
      || periods[0];
    periodSelect.value = selected.period;
    if (["3", "6", "12"].includes(requested.get("months"))) monthsSelect.value = requested.get("months");
    periodSelect.disabled = false;
    await load();
  } catch (problem) {
    showError(error, problem.message);
  }
}

periodSelect.addEventListener("change", load);
monthsSelect.addEventListener("change", load);
topicSelect.addEventListener("change", renderTopics);
init();
