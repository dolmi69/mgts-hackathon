import {
  analysisStatus,
  apiGet,
  apiRequest,
  escapeHtml,
  formatAverage,
  formatPeriod,
  formatScore,
  hideError,
  initials,
  plural,
  priorityBadge,
  ruleLabel,
  setupChartDefaults,
  showError,
  topicLabel,
  trendHtml,
  waitingBadge,
} from "./api.js";

const state = {
  periods: [],
  selected: null,
  summary: null,
  charts: [],
  // Focus block: signals being saved right now and the last error per signal.
  taking: new Set(),
  focusErrors: new Map(),
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
  els.mentorsBody = document.getElementById("mentors-body");
  els.mentorsTable = document.querySelector(".mentors-table");
  els.mentorsEmpty = document.getElementById("mentors-empty");
  els.topicsEmpty = document.getElementById("topics-empty");
  els.asOf = document.getElementById("as-of");
  els.demoFeedback = document.getElementById("demo-feedback-link");
  els.dynamics = document.getElementById("dynamics-chart");
  els.topics = document.getElementById("topics-chart");
  els.focus = document.getElementById("focus");
  els.focusHint = document.getElementById("focus-hint");
  els.focusList = document.getElementById("focus-list");
  els.focusEmpty = document.getElementById("focus-empty");
  els.focusStatus = document.getElementById("focus-status");
}

function plainReasons(item) {
  // The first reason is shown on its own line, the rest go under «ещё N».
  const main = item.main_reason || (item.reasons || [])[0] || "Есть сигнал по ответам опроса";
  return { main, rest: (item.reasons || []).filter((reason) => reason !== main) };
}

function focusAction(item) {
  if (item.state === "in_progress") {
    return `
      <span class="badge low">В работе</span>
      <a class="focus-link" href="signals.html?intern_id=${encodeURIComponent(item.intern_id)}"
        aria-label="Открыть во входящих: ${escapeHtml(item.name)}">Открыть во входящих</a>
    `;
  }
  const busy = state.taking.has(item.signal_id);
  const label = busy ? "Сохраняем…" : "Взять в работу";
  return `
    <button type="button" class="button" data-take="${escapeHtml(item.signal_id)}"
      aria-disabled="${busy}" aria-label="${label}: ${escapeHtml(item.name)}">${label}</button>
  `;
}

function renderFocus(summary) {
  // An older backend has no focus list: the rest of the page still works.
  els.focus.hidden = !Array.isArray(summary.focus);
  if (els.focus.hidden) return;
  const items = summary.focus.slice(0, 5);
  els.focusHint.textContent = items.length
    ? `Сигналы за ${summary.period_label}. Начните сверху: откройте карточку и возьмите сигнал в работу.`
    : `Сигналы за ${summary.period_label}.`;
  els.focusList.hidden = !items.length;
  els.focusEmpty.hidden = Boolean(items.length);
  if (!items.length) {
    const answered = summary.kpis.responded || summary.kpis.mentor_responded;
    els.focusEmpty.innerHTML = answered
      ? "<strong>Срочных дел нет.</strong> За этот месяц важных сигналов нет или все они разобраны."
      : "<strong>Пока нечего разбирать.</strong> Ответов за этот месяц ещё нет.";
  }
  els.focusList.innerHTML = items
    .map((item) => {
      const reasons = plainReasons(item);
      const more = reasons.rest.length
        ? `<details class="more">
            <summary>ещё ${reasons.rest.length}<span class="visually-hidden"> ${plural(reasons.rest.length, "причина", "причины", "причин")}: ${escapeHtml(item.name)}</span></summary>
            <ul>${reasons.rest.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
          </details>`
        : "";
      const error = state.focusErrors.get(item.signal_id);
      const card = `intern.html?id=${encodeURIComponent(item.intern_id)}`;
      return `
        <li class="focus-item ${escapeHtml(item.severity)}" data-signal="${escapeHtml(item.signal_id)}">
          <div class="focus-person">
            <a class="focus-name" href="${card}">${escapeHtml(item.name)}</a>
            <small>${item.mentor_name ? `Наставник: ${escapeHtml(item.mentor_name)}` : "Без наставника"}</small>
          </div>
          <div class="focus-why">
            <p class="focus-reason">${escapeHtml(reasons.main)}</p>
            <div class="focus-meta">
              ${priorityBadge(item.severity)}
              ${waitingBadge(item.waiting_days, item.overdue)}
            </div>
            ${more}
          </div>
          <div class="focus-actions">${focusAction(item)}</div>
          ${error ? `<p class="focus-error" role="alert">${escapeHtml(error)}</p>` : ""}
        </li>
      `;
    })
    .join("");
}

function takeErrorText(error) {
  if (error.status === 403) {
    return "У вас доступ только для просмотра. Попросите коллегу с учётной записью HR взять сигнал в работу.";
  }
  if (error.status === 409) {
    return `${error.message} Нажмите «Обновить», чтобы увидеть актуальное состояние.`;
  }
  return error.message;
}

async function takeSignal(event) {
  const button = event.target.closest("[data-take]");
  if (!button) return;
  const signalId = Number(button.dataset.take);
  // Guards against a double click: one request per signal at a time.
  if (state.taking.has(signalId)) return;
  const name = state.summary.focus.find((item) => item.signal_id === signalId)?.name || "";
  state.taking.add(signalId);
  state.focusErrors.delete(signalId);
  button.setAttribute("aria-disabled", "true");
  button.textContent = "Сохраняем…";
  els.focusStatus.textContent = "Сохраняем…";
  let taken = false;
  try {
    await apiRequest(`/api/signals/${encodeURIComponent(signalId)}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: "in_progress", assigned_to: "", note: "" }),
    });
    taken = true;
    els.focusStatus.textContent = `Сигнал взят в работу: ${name}.`;
  } catch (error) {
    state.focusErrors.set(signalId, takeErrorText(error));
    els.focusStatus.textContent = "";
  }
  state.taking.delete(signalId);
  // The month may have changed while saving: update only if the item is still listed.
  const item = state.summary.focus?.find((entry) => entry.signal_id === signalId);
  if (!item) return;
  if (taken) Object.assign(item, { state: "in_progress", waiting_days: null, overdue: false });
  const active = document.activeElement;
  const keepFocus = active === document.body || active === button;
  renderFocus(state.summary);
  if (keepFocus) {
    els.focusList
      .querySelector(`[data-signal="${signalId}"] .focus-actions :is(a, button)`)
      ?.focus({ preventScroll: true });
  }
}

function destroyCharts() {
  state.charts.forEach((chart) => chart.destroy());
  state.charts = [];
}

function renderKpis(summary) {
  const kpis = summary.kpis;
  const responseRate = Math.round((kpis.response_rate || 0) * 100);
  const medium = kpis.flags_medium || 0;
  const low = kpis.flags_low || 0;
  const flagsHint = `ещё ${medium} ${plural(medium, "средний", "средних", "средних")} · ${low} ${plural(low, "наблюдение", "наблюдения", "наблюдений")}`;
  els.kpis.innerHTML = `
    <article class="kpi">
      <div class="kpi-label">Стажёров</div>
      <div class="kpi-value">${escapeHtml(kpis.interns)}</div>
      <div class="kpi-hint">в программе</div>
    </article>
    <article class="kpi">
      <div class="kpi-label">Ответили стажёры</div>
      <div class="kpi-value">${escapeHtml(kpis.responded)}</div>
      <div class="kpi-hint">${responseRate}% участия · наставники ${escapeHtml(kpis.mentor_responded)}</div>
    </article>
    <article class="kpi">
      <div class="kpi-label">Средняя оценка</div>
      <div class="kpi-value">${escapeHtml(formatAverage(kpis.avg_score))}</div>
      <div class="kpi-hint">по ответам стажёров</div>
    </article>
    <article class="kpi">
      <div class="kpi-label">Важные сигналы</div>
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
            ticks: { stepSize: 1, color: "#6e6e73" },
            grid: { color: "#f0f0f2" },
            border: { display: false },
          },
          x: {
            ticks: { color: "#6e6e73", maxRotation: 0, autoSkip: true },
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
            ticks: { precision: 0, color: "#6e6e73" },
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

function reasonCell(intern) {
  const flags = intern.flags || [];
  if (!flags.length) return '<span class="badge positive">Сигналов нет</span>';
  const priority = intern.priority || flags[0].severity;
  const reason = intern.main_reason || ruleLabel(flags[0].rule_code);
  const rest = flags.length - 1;
  const details = flags
    .map(
      (flag) => `
        <li>
          <strong>${escapeHtml(ruleLabel(flag.rule_code))}</strong>
          <span>${escapeHtml(flag.explanation)}</span>
          <small>правило ${escapeHtml(flag.rule_code)}</small>
        </li>`
    )
    .join("");
  return `
    <div class="reason">
      ${priorityBadge(priority)}
      <span class="reason-main">${escapeHtml(reason)}</span>
    </div>
    <details class="more">
      <summary>${rest ? `ещё ${rest}` : "почему"}<span class="visually-hidden">, подробнее: ${escapeHtml(intern.name)}</span></summary>
      <ul>${details}</ul>
    </details>
  `;
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
          <td>${reasonCell(intern)}</td>
          <td>${analysisStatus(intern.analysis_status)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderMentors(summary) {
  const mentors = summary.mentors || [];
  if (!mentors.length) {
    els.mentorsTable.hidden = true;
    els.mentorsEmpty.hidden = false;
    return;
  }
  els.mentorsTable.hidden = false;
  els.mentorsEmpty.hidden = true;
  const names = new Map((summary.interns || []).map((intern) => [intern.id, intern.name]));
  els.mentorsBody.innerHTML = mentors
    .map((mentor) => {
      const share = Math.round((mentor.share_with_flags || 0) * 100);
      const high = mentor.interns_with_high
        ? `<span class="badge high">с высоким приоритетом: ${escapeHtml(mentor.interns_with_high)}</span>`
        : "";
      const flagged = (mentor.flagged_intern_ids || [])
        .map(
          (id) =>
            `<a class="chip" href="intern.html?id=${encodeURIComponent(id)}">${escapeHtml(names.get(id) || `#${id}`)}</a>`
        )
        .join("");
      const status = mentor.needs_attention
        ? '<span class="badge medium mentor-note">Повод обсудить нагрузку и поддержку наставника</span>'
        : '<span class="badge neutral">без особенностей</span>';
      return `
        <tr class="${mentor.needs_attention ? "mentor-attention" : ""}">
          <td>
            <span class="intern-link mentor-name">
              <span class="avatar">${escapeHtml(initials(mentor.name))}</span>
              <span>
                <strong>${escapeHtml(mentor.name)}</strong>
                <small>стажёров в месяце: ${escapeHtml(mentor.interns_total)}</small>
              </span>
            </span>
          </td>
          <td>
            <div class="mentor-share">
              <span class="score">${escapeHtml(mentor.interns_with_flags)} из ${escapeHtml(mentor.interns_total)}</span>
              <span class="muted small">${share}%</span>
              ${high}
            </div>
            ${flagged ? `<div class="chips mentor-interns">${flagged}</div>` : ""}
          </td>
          <td>${escapeHtml(mentor.mentor_responded)} из ${escapeHtml(mentor.interns_total)}</td>
          <td><span class="score">${escapeHtml(formatAverage(mentor.avg_intern_score))}</span></td>
          <td>${status}</td>
        </tr>
      `;
    })
    .join("");
}

function render(summary) {
  els.demoFeedback.hidden = !summary.demo_feedback_enabled;
  els.asOf.textContent = `Последние ответы: ${formatPeriod(summary.as_of)}.`;
  renderFocus(summary);
  renderKpis(summary);
  renderCharts(summary);
  renderTable(summary);
  renderMentors(summary);
}

let loadSeq = 0;

async function load(period) {
  // Ignore responses that arrive after the user picked another month.
  const seq = ++loadSeq;
  hideError(els.error);
  els.refresh.disabled = true;
  els.refresh.textContent = "Загрузка…";
  try {
    const query = period ? `?period=${encodeURIComponent(period)}` : "";
    const summary = await apiGet(`/api/dashboard/summary${query}`);
    if (seq !== loadSeq) return;
    state.summary = summary;
    state.selected = summary.period;
    state.focusErrors.clear();
    render(summary);
    const url = new URL(window.location.href);
    url.searchParams.set("period", summary.period);
    window.history.replaceState({}, "", url);
  } catch (error) {
    if (seq === loadSeq) {
      showError(els.error, error.message);
      // Keep the month in the list equal to the data on the screen.
      if (state.selected) els.period.value = state.selected;
    }
  } finally {
    if (seq === loadSeq) {
      els.refresh.disabled = false;
      els.refresh.textContent = "Обновить";
    }
  }
}

async function init() {
  cacheElements();
  els.period.addEventListener("change", () => load(els.period.value));
  els.refresh.addEventListener("click", () => load(state.selected));
  els.focusList.addEventListener("click", takeSignal);
  try {
    const periods = await apiGet("/api/periods");
    state.periods = periods;
    if (!periods.length) {
      const summary = await apiGet("/api/dashboard/summary");
      els.demoFeedback.hidden = !summary.demo_feedback_enabled;
      els.asOf.textContent = "Данных пока нет.";
      els.focus.hidden = false;
      els.focusHint.hidden = els.focusList.hidden = true;
      els.focusEmpty.hidden = false;
      els.focusEmpty.innerHTML =
        "<strong>Пока нечего разбирать.</strong> Когда придут первые ответы на анкету, здесь появятся люди, которым нужно внимание.";
      els.kpis.innerHTML = "";
      els.table.hidden = true;
      els.empty.hidden = false;
      els.mentorsTable.hidden = true;
      els.mentorsEmpty.hidden = false;
      return;
    }
    els.period.innerHTML = periods
      .map((item) => `<option value="${escapeHtml(item.period)}">${escapeHtml(item.label)}</option>`)
      .join("");
    const requested = new URLSearchParams(window.location.search).get("period");
    // Months come from the survey schedule, so the newest one can be empty:
    // open the last month that has answers.
    const selected = periods.find((item) => item.period === requested)
      || periods.find((item) => item.has_answers)
      || periods[0];
    els.period.value = selected.period;
    await load(selected.period);
  } catch (error) {
    showError(els.error, error.message);
  }
}

window.addEventListener("DOMContentLoaded", init);
