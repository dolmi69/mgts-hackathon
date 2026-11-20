import {
  analysisStatus,
  apiGet,
  escapeHtml,
  flagChip,
  formatAverage,
  formatPeriod,
  formatScore,
  hideError,
  initials,
  setupChartDefaults,
  showError,
  topicLabel,
  trendHtml,
} from "./api.js";

const state = {
  periods: [],
  selected: null,
  summary: null,
  charts: [],
};

const els = {};

function cacheElements() {
  els.error = document.getElementById("error");
  els.period = document.getElementById("period-select");
  els.refresh = document.getElementById("refresh");
  els.kpis = document.getElementById("kpis");
  els.body = document.getElementById("interns-body");
  els.table = document.querySelector(".interns-table");
  els.empty = document.getElementById("interns-empty");
  els.topicsEmpty = document.getElementById("topics-empty");
  els.asOf = document.getElementById("as-of");
  els.demoFeedback = document.getElementById("demo-feedback-link");
  els.dynamics = document.getElementById("dynamics-chart");
  els.topics = document.getElementById("topics-chart");
}

function destroyCharts() {
  state.charts.forEach((chart) => chart.destroy());
  state.charts = [];
}

function renderKpis(summary) {
  const kpis = summary.kpis;
  const responseRate = Math.round((kpis.response_rate || 0) * 100);
  const flagsHint = `${kpis.flags_medium || 0} средних · ${kpis.flags_low || 0} наблюдений`;
  els.kpis.innerHTML = `
    <article class="kpi">
      <div class="kpi-label">Стажёров</div>
      <div class="kpi-value">${escapeHtml(kpis.interns)}</div>
      <div class="kpi-hint">в программе</div>
    </article>
    <article class="kpi">
      <div class="kpi-label">Ответили</div>
      <div class="kpi-value">${escapeHtml(kpis.responded)}</div>
      <div class="kpi-hint">${responseRate}% участия</div>
    </article>
    <article class="kpi">
      <div class="kpi-label">Средняя оценка</div>
      <div class="kpi-value">${escapeHtml(formatAverage(kpis.avg_score))}</div>
      <div class="kpi-hint">по ответам стажёров</div>
    </article>
    <article class="kpi">
      <div class="kpi-label">Высокие сигналы</div>
      <div class="kpi-value ${kpis.flags_high ? "alarm" : ""}">${escapeHtml(kpis.flags_high)}</div>
      <div class="kpi-hint">${escapeHtml(flagsHint)}</div>
    </article>
  `;
}

function renderCharts(summary) {
  destroyCharts();
  if (typeof window.Chart === "undefined") {
    document.querySelectorAll(".chart-wrap").forEach((wrap) => {
      wrap.innerHTML = '<div class="chart-fallback">График недоступен: не загрузился Chart.js</div>';
    });
    return;
  }

  setupChartDefaults(window.Chart);
  const dynamicsLabels = summary.dynamics.map((point) => point.period_label);
  const internData = summary.dynamics.map((point) => point.intern_avg);
  const mentorData = summary.dynamics.map((point) => point.mentor_avg);

  state.charts.push(
    new window.Chart(els.dynamics, {
      type: "line",
      data: {
        labels: dynamicsLabels,
        datasets: [
          {
            label: "Стажёры",
            data: internData,
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
            label: "Наставники",
            data: mentorData,
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
    })
  );

  const topics = summary.top_topics || [];
  if (!topics.length) {
    els.topics.hidden = true;
    els.topicsEmpty.hidden = false;
    return;
  }
  els.topics.hidden = false;
  els.topicsEmpty.hidden = true;
  state.charts.push(
    new window.Chart(els.topics, {
      type: "bar",
      data: {
        labels: topics.map((topic) => topicLabel(topic.topic)),
        datasets: [
          {
            label: "Упоминаний",
            data: topics.map((topic) => topic.count),
            backgroundColor: "#0071e3",
            borderRadius: 8,
            barThickness: 16,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: {
            beginAtZero: true,
            ticks: { precision: 0, color: "#86868b" },
            grid: { color: "#f0f0f2" },
            border: { display: false },
          },
          y: {
            ticks: { color: "#6e6e73" },
            grid: { display: false },
            border: { display: false },
          },
        },
      },
    })
  );
}

function renderTable(summary) {
  const interns = summary.interns || [];
  if (!interns.length) {
    els.table.hidden = true;
    els.empty.hidden = false;
    return;
  }
  els.table.hidden = false;
  els.empty.hidden = true;
  els.body.innerHTML = interns
    .map((intern) => {
      const flags = intern.flags || [];
      const visibleFlags = flags.slice(0, 3).map((flag) => flagChip(flag)).join("");
      const hiddenCount = flags.length > 3 ? `<span class="badge neutral">+${flags.length - 3}</span>` : "";
      const flagsHtml = flags.length
        ? `<div class="chips">${visibleFlags}${hiddenCount}</div>`
        : '<span class="badge positive">нет сигналов</span>';
      return `
        <tr>
          <td>
            <a class="intern-link" href="intern.html?id=${encodeURIComponent(intern.id)}">
              <span class="avatar">${escapeHtml(initials(intern.name))}</span>
              <span>
                <strong>${escapeHtml(intern.name)}</strong>
                <small>${escapeHtml(intern.direction || "Направление не указано")}</small>
              </span>
            </a>
          </td>
          <td>${escapeHtml(intern.mentor_name || "—")}</td>
          <td>
            <span class="score">${escapeHtml(formatScore(intern.avg_score))}</span>
            ${trendHtml(intern.trend)}
          </td>
          <td>${flagsHtml}</td>
          <td>${analysisStatus(intern.analysis_status)}</td>
        </tr>
      `;
    })
    .join("");
}

function render(summary) {
  els.demoFeedback.hidden = !summary.demo_feedback_enabled;
  els.asOf.textContent = `Данные: ${formatPeriod(summary.as_of)}`;
  renderKpis(summary);
  renderCharts(summary);
  renderTable(summary);
}

async function load(period) {
  hideError(els.error);
  els.refresh.disabled = true;
  els.refresh.textContent = "Загрузка…";
  try {
    const query = period ? `?period=${encodeURIComponent(period)}` : "";
    const summary = await apiGet(`/api/dashboard/summary${query}`);
    state.summary = summary;
    state.selected = summary.period;
    render(summary);
    const url = new URL(window.location.href);
    url.searchParams.set("period", summary.period);
    window.history.replaceState({}, "", url);
  } catch (error) {
    showError(els.error, error.message);
  } finally {
    els.refresh.disabled = false;
    els.refresh.textContent = "Обновить";
  }
}

async function init() {
  cacheElements();
  els.period.addEventListener("change", () => load(els.period.value));
  els.refresh.addEventListener("click", () => load(state.selected));
  try {
    const periods = await apiGet("/api/periods");
    state.periods = periods;
    if (!periods.length) {
      const summary = await apiGet("/api/dashboard/summary");
      els.demoFeedback.hidden = !summary.demo_feedback_enabled;
      els.asOf.textContent = "Нет данных";
      els.kpis.innerHTML = "";
      els.table.hidden = true;
      els.empty.hidden = false;
      return;
    }
    els.period.innerHTML = periods
      .map((item) => `<option value="${escapeHtml(item.period)}">${escapeHtml(item.label)}</option>`)
      .join("");
    const requested = new URLSearchParams(window.location.search).get("period");
    const selected = periods.find((item) => item.period === requested) || periods[0];
    els.period.value = selected.period;
    await load(selected.period);
  } catch (error) {
    showError(els.error, error.message);
  }
}

window.addEventListener("DOMContentLoaded", init);
