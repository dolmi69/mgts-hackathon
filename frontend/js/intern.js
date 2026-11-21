import {
  analysisStatus,
  apiGet,
  apiRequest,
  escapeHtml,
  flagChip,
  formatAverage,
  formatDate,
  formatDateTime,
  formatPeriod,
  formatScore,
  hideError,
  initials,
  sentimentBadge,
  setupChartDefaults,
  showError,
  topicChips,
} from "./api.js";

const state = { card: null, chart: null };
const els = {};

function cacheElements() {
  els.error = document.getElementById("error");
  els.avatar = document.getElementById("avatar");
  els.name = document.getElementById("intern-name");
  els.meta = document.getElementById("intern-meta");
  els.stats = document.getElementById("profile-stats");
  els.flags = document.getElementById("flags");
  els.flagsHint = document.getElementById("flags-hint");
  els.timeline = document.getElementById("timeline");
  els.chart = document.getElementById("score-chart");
  els.inbox = document.getElementById("intern-inbox-link");
}

function stat(value, label) {
  return `<div class="stat"><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></div>`;
}

function renderProfile(card) {
  document.title = `${card.name} · МГТС`;
  els.avatar.textContent = initials(card.name);
  els.name.textContent = card.name;
  const meta = [
    card.direction || "Направление не указано",
    card.mentor_name ? `наставник: ${card.mentor_name}` : null,
    card.start_period ? `старт: ${formatPeriod(card.start_period)}` : null,
  ].filter(Boolean);
  els.meta.textContent = meta.join(" · ");
  const params = new URLSearchParams({ intern_id: card.id });
  if (card.is_demo) params.set("scope", "demo");
  els.inbox.href = `signals.html?${params}`;
  els.inbox.hidden = false;
  els.stats.innerHTML = [
    stat(card.stats.total_answers, "ответов"),
    stat(formatAverage(card.stats.avg_intern), "средняя стажёра"),
    stat(formatAverage(card.stats.avg_mentor), "средняя наставника"),
    stat(card.flags.length, "сигналов"),
  ].join("");
}

function renderFlags(card) {
  if (!card.flags.length) {
    els.flagsHint.textContent = "Каждый флажок объяснён и связан с ответом";
    els.flags.innerHTML =
      '<div class="flags-ok">Сигналов нет. Это не значит «всё идеально», это значит, что данные не расходятся между собой.</div>';
    return;
  }
  const scrollable =
    card.flags.length > 2 && window.matchMedia("(min-width: 981px)").matches;
  els.flagsHint.textContent = scrollable
    ? `${card.flags.length} сигналов · прокрутите, чтобы увидеть все`
    : "Каждый флажок объяснён и связан с ответом";
  els.flags.innerHTML = card.flags
    .map((flag) => {
      const links = (flag.evidence_answer_ids || [])
        .map(
          (answerId) =>
            `<a href="#answer-${encodeURIComponent(answerId)}">ответ №${escapeHtml(answerId)}</a>`
        )
        .join(", ");
      return `
        <article class="flag-card ${escapeHtml(flag.severity)}">
          <div class="flag-head">
            ${flagChip(flag, false)}
            <span class="small muted">${escapeHtml(flag.period_label)}</span>
          </div>
          <h3>${escapeHtml(flag.title)}</h3>
          <p>${escapeHtml(flag.explanation)}</p>
          ${links ? `<p class="flag-source">Источник: ${links}</p>` : ""}
        </article>
      `;
    })
    .join("");
}

function renderChart(card) {
  if (typeof window.Chart === "undefined") {
    document.querySelector(".chart-wrap").innerHTML =
      '<div class="chart-fallback">График недоступен: не загрузился Chart.js</div>';
    return;
  }
  if (state.chart) state.chart.destroy();
  setupChartDefaults(window.Chart);
  const series = card.series || [];
  if (!series.length) {
    document.querySelector(".chart-wrap").innerHTML =
      '<div class="chart-fallback">Пока нет данных для графика.</div>';
    return;
  }
  state.chart = new window.Chart(els.chart, {
    type: "line",
    data: {
      labels: series.map((point) => point.period_label),
      datasets: [
        {
          label: "Стажёр",
          data: series.map((point) => point.intern_score),
          borderColor: "#0071e3",
          backgroundColor: "rgba(0, 113, 227, 0.08)",
          pointBackgroundColor: "#0071e3",
          pointBorderColor: "#ffffff",
          pointBorderWidth: 2,
          borderWidth: 2.5,
          pointRadius: 3.5,
          pointHoverRadius: 6,
          tension: 0.25,
          spanGaps: true,
        },
        {
          label: "Наставник",
          data: series.map((point) => point.mentor_score),
          borderColor: "#ff9500",
          backgroundColor: "rgba(255, 149, 0, 0.06)",
          pointBackgroundColor: "#ff9500",
          pointBorderColor: "#ffffff",
          pointBorderWidth: 2,
          borderWidth: 2.5,
          pointRadius: 3.5,
          pointHoverRadius: 6,
          tension: 0.25,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            color: "#6e6e73",
            boxWidth: 8,
            boxHeight: 8,
            usePointStyle: true,
            padding: 18,
          },
        },
        tooltip: {
          callbacks: {
            label: (context) =>
              `${context.dataset.label}: ${formatScore(context.parsed.y)}`,
          },
        },
      },
      scales: {
        y: {
          min: 1,
          max: 5,
          ticks: { stepSize: 1, color: "#86868b" },
          grid: { color: "#f0f0f2" },
          border: { display: false },
        },
        x: {
          ticks: { color: "#86868b", maxRotation: 0, autoSkip: true },
          grid: { display: false },
          border: { display: false },
        },
      },
    },
  });
}

const SCORE_LABELS = {
  overall: "Общая",
  workload: "Нагрузка",
  mentorship: "Наставничество",
};

function renderAnalysis(answer) {
  if (["pending", "processing"].includes(answer.analysis_status)) {
    return '<div class="analysis"><span class="badge pending">анализ идёт</span><p class="analysis-summary">Комментарий уже сохранён, разметка появится после обработки.</p></div>';
  }
  if (answer.analysis_status === "failed") {
    const retry = state.card.can_write ? `
      <button type="button" class="button" data-retry-analysis="${escapeHtml(answer.id)}">
        Повторить анализ
      </button>
      <p class="analysis-summary" role="status"></p>
    ` : "";
    return `
      <div class="analysis">
        <span class="badge failed">ошибка анализа</span>
        <p class="analysis-summary">Модель не смогла разобрать комментарий. Исходный ответ сохранён.</p>
        ${retry}
      </div>
    `;
  }
  const analysis = answer.analysis;
  if (!analysis || analysis.status !== "done") {
    return '<div class="analysis"><span class="badge neutral">без анализа</span></div>';
  }
  const evidence = (analysis.evidence || [])
    .map((quote) => `<li>«${escapeHtml(quote)}»</li>`)
    .join("");
  return `
    <div class="analysis">
      <div class="chips">
        ${sentimentBadge(analysis.sentiment)}
        ${topicChips(analysis.topics || [])}
      </div>
      <p class="analysis-summary">${escapeHtml(analysis.summary || "Резюме не сформировано.")}</p>
      ${evidence ? `<ul class="evidence">${evidence}</ul>` : ""}
    </div>
  `;
}

function renderAnswer(answer, roleLabel) {
  if (!answer) {
    return `<div class="answer"><div class="answer-empty">Ответа за этот месяц нет.</div></div>`;
  }
  const scores = Object.entries(answer.scores || {})
    .map(
      ([key, value]) =>
        `<tr><td>${escapeHtml(SCORE_LABELS[key] || key)}</td><td>${escapeHtml(formatScore(value))}</td></tr>`
    )
    .join("");
  return `
    <article class="answer" id="answer-${escapeHtml(answer.id)}">
      <div class="answer-head">
        <span class="answer-role">${escapeHtml(roleLabel)}</span>
        <span class="answer-score">${escapeHtml(formatScore(answer.overall_score))}</span>
      </div>
      <div class="answer-meta">${escapeHtml(formatDateTime(answer.submitted_at))}</div>
      <blockquote class="comment">${escapeHtml(answer.comment || "Комментарий не заполнен.")}</blockquote>
      ${renderAnalysis(answer)}
      <details>
        <summary>Исходные данные ответа</summary>
        <table class="raw-table">${scores}</table>
        <p class="answer-meta">Статус анализа: ${analysisStatus(answer.analysis_status)}</p>
      </details>
    </article>
  `;
}

function renderTimeline(card) {
  const byPeriod = new Map();
  for (const point of card.series || []) byPeriod.set(point.period, { point, answers: [] });
  for (const answer of card.answers || []) {
    if (!byPeriod.has(answer.period)) {
      byPeriod.set(answer.period, { point: { period: answer.period, period_label: answer.period_label }, answers: [] });
    }
    byPeriod.get(answer.period).answers.push(answer);
  }
  for (const flag of card.flags || []) {
    if (!byPeriod.has(flag.period)) {
      byPeriod.set(flag.period, { point: { period: flag.period, period_label: flag.period_label }, answers: [] });
    }
  }

  const periods = [...byPeriod.values()].sort((a, b) => (a.point.period < b.point.period ? 1 : -1));
  if (!periods.length) {
    els.timeline.innerHTML = '<div class="empty">Ответов пока нет.</div>';
    return;
  }

  els.timeline.innerHTML = periods
    .map(({ point, answers }) => {
      const internAnswer = answers.find(
        (answer) => answer.respondent_type === "intern" && answer.current_for_month !== false
      );
      const mentorAnswer = answers.find(
        (answer) => answer.respondent_type === "mentor" && answer.current_for_month !== false
      );
      const older = answers.filter((answer) => answer.current_for_month === false);
      const flags = (card.flags || []).filter((flag) => flag.period === point.period);
      const flagHtml = flags.length
        ? `<div class="chips">${flags.map((flag) => flagChip(flag)).join("")}</div>`
        : "";
      return `
        <section class="month-card">
          <header class="month-head">
            <h3>${escapeHtml(point.period_label)}</h3>
            ${flagHtml}
          </header>
          <div class="month-body">
            ${renderAnswer(internAnswer, "Стажёр")}
            ${renderAnswer(mentorAnswer, "Наставник")}
          </div>
          ${older.length ? `
            <details class="month-history">
              <summary>Предыдущие ответы за месяц · ${older.length}</summary>
              ${older.map((answer) => renderAnswer(
                answer, answer.respondent_type === "intern" ? "Стажёр" : "Наставник"
              )).join("")}
            </details>
          ` : ""}
        </section>
      `;
    })
    .join("");
}

function render(card) {
  state.card = card;
  renderProfile(card);
  renderFlags(card);
  renderChart(card);
  renderTimeline(card);
  const answerId = /^#(answer-\d+)$/.exec(window.location.hash)?.[1];
  if (answerId) {
    const answer = document.getElementById(answerId);
    const older = answer?.closest(".month-history");
    if (older) older.open = true;
    answer?.scrollIntoView({ block: "center" });
  }
}

async function retryAnalysis(event) {
  const button = event.target.closest("[data-retry-analysis]");
  if (!button || button.disabled) return;
  const answerId = Number(button.dataset.retryAnalysis);
  const path = `/api/interns/${state.card.id}`;
  const status = button.parentElement.querySelector('[role="status"]');
  button.disabled = true;
  status.textContent = "Ответ отправлен на повторный анализ…";
  try {
    await apiRequest(`${path}/answers/${answerId}/retry`, { method: "POST" });
    for (let attempt = 0; attempt < 10; attempt += 1) {
      const card = await apiGet(path);
      const answer = card.answers.find((item) => item.id === answerId);
      if (!answer || !["pending", "processing"].includes(answer.analysis_status) || attempt === 9) {
        render(card);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  } catch (error) {
    status.textContent = error.message;
    button.disabled = false;
  }
}

async function init() {
  cacheElements();
  els.timeline.addEventListener("click", retryAnalysis);
  const id = new URLSearchParams(window.location.search).get("id");
  if (!id || !/^\d+$/.test(id)) {
    showError(els.error, "Не указан стажёр. Вернитесь к списку и выберите карточку.");
    return;
  }
  try {
    const card = await apiGet(`/api/interns/${encodeURIComponent(id)}`);
    render(card);
  } catch (error) {
    showError(els.error, error.message);
  }
}

window.addEventListener("DOMContentLoaded", init);
