const MONTHS = [
  "январь", "февраль", "март", "апрель", "май", "июнь",
  "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
];

export async function apiGet(path) {
  return apiRequest(path);
}

export async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { Accept: "application/json", ...options.headers },
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch (_) {
    // The browser text ("Failed to fetch") means nothing to an HR user.
    throw apiError("Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз.", 0);
  }
  if (response.status === 401) {
    throw apiError("Требуется авторизация. Обновите страницу и введите логин и пароль.", 401);
  }
  if (!response.ok) {
    let message = `Ошибка API: ${response.status}`;
    try {
      const body = await response.json();
      if (body && body.detail) {
        message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch (_) {
      // keep the status message
    }
    throw apiError(message, response.status);
  }
  return response.json();
}

function apiError(message, status) {
  return Object.assign(new Error(message), { status });
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function formatPeriod(value) {
  if (!value) return "—";
  const match = String(value).match(/^(\d{4})-(\d{2})/);
  if (!match) return String(value);
  const month = Number(match[2]);
  return `${MONTHS[month - 1] || match[2]} ${match[1]}`;
}

export function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(date);
}

export function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "—";
  return parts.slice(0, 2).map((part) => part[0].toUpperCase()).join("");
}

export function formatScore(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return Number.isInteger(number) ? String(number) : number.toFixed(1).replace(".", ",");
}

export function formatAverage(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(1).replace(".", ",");
}

export function trendHtml(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return '<span class="trend flat">—</span>';
  }
  const number = Number(value);
  if (Math.abs(number) < 0.05) return '<span class="trend flat">0</span>';
  const sign = number > 0 ? "+" : "−";
  const className = number > 0 ? "up" : "down";
  const formatted = Math.abs(number).toFixed(1).replace(".", ",");
  return `<span class="trend ${className}">${sign}${formatted}</span>`;
}

export function setupChartDefaults(Chart) {
  Chart.defaults.font.family =
    '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = "#6e6e73";
  Chart.defaults.plugins.tooltip.backgroundColor = "rgba(29, 29, 31, 0.92)";
  Chart.defaults.plugins.tooltip.titleColor = "#ffffff";
  Chart.defaults.plugins.tooltip.bodyColor = "#f5f5f7";
  Chart.defaults.plugins.tooltip.cornerRadius = 10;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.displayColors = false;
}

export const TOPIC_LABELS = {
  workload: "нагрузка",
  tasks: "задачи",
  mentorship: "наставничество",
  team: "команда",
  process: "процессы",
  growth: "развитие",
  leave: "намерение уйти",
  other: "другое",
};

export const SENTIMENT_LABELS = {
  positive: "позитив",
  neutral: "нейтрально",
  mixed: "смешанное",
  negative: "негатив",
};

export const SEVERITY_LABELS = {
  high: "высокий",
  medium: "средний",
  low: "наблюдение",
};

// One priority per person, in full words: colour alone must not carry the meaning.
export const PRIORITY_LABELS = {
  high: "Высокий приоритет",
  medium: "Средний приоритет",
  low: "Наблюдение",
};

// Same wording as the backend plain reasons, so one signal has one name everywhere.
export const RULE_LABELS = {
  R1: "Оценка высокая, но в комментарии есть замечание",
  R2: "Стажёр и наставник видят месяц по-разному",
  R3: "Оценки снижаются несколько месяцев",
  R4: "Не ответил на опрос",
  R5: "Одна и та же трудность повторяется",
  R6: "Низкая оценка месяца",
  R7: "В ответе есть тема ухода",
};

export function ruleLabel(code) {
  return RULE_LABELS[code] || "Есть сигнал";
}

export function topicLabel(topic) {
  return TOPIC_LABELS[topic] || topic || "другое";
}

export function sentimentBadge(sentiment) {
  if (!sentiment) return '<span class="badge neutral">без анализа</span>';
  const label = SENTIMENT_LABELS[sentiment] || sentiment;
  return `<span class="badge ${escapeHtml(sentiment)}">${escapeHtml(label)}</span>`;
}

export function severityBadge(severity) {
  const label = SEVERITY_LABELS[severity] || severity || "—";
  return `<span class="badge ${escapeHtml(severity || "neutral")}">${escapeHtml(label)}</span>`;
}

export function priorityBadge(severity) {
  const known = Object.hasOwn(PRIORITY_LABELS, severity) ? severity : "neutral";
  return `<span class="badge ${known}">${known === "neutral" ? "Без приоритета" : PRIORITY_LABELS[known]}</span>`;
}

export function flagChip(flag) {
  return `<span class="badge ${escapeHtml(flag.severity || "low")}">${escapeHtml(ruleLabel(flag.rule_code))}</span>`;
}

export function plural(count, one, few, many) {
  const tens = Math.abs(count) % 100;
  const units = tens % 10;
  if (tens > 10 && tens < 20) return many;
  if (units === 1) return one;
  return units >= 2 && units <= 4 ? few : many;
}

export function waitingText(days) {
  const count = Number(days);
  if (days === null || days === undefined || !Number.isFinite(count)) return "";
  if (count < 1) return "появился сегодня";
  return `ждёт ${count} ${plural(count, "день", "дня", "дней")}`;
}

// Text, not only colour, says that the signal has waited too long.
export function waitingBadge(days, overdue) {
  const text = waitingText(days);
  if (overdue) return `<span class="badge overdue">Просрочено${text ? ` · ${text}` : ""}</span>`;
  return text ? `<span class="badge neutral">${text}</span>` : "";
}

// navigator.clipboard exists only in a secure context (https or localhost).
export async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      // permission denied: try the old way
    }
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.className = "visually-hidden";
  area.setAttribute("readonly", "");
  area.setAttribute("aria-hidden", "true");
  document.body.append(area);
  const previous = document.activeElement;
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (_) {
    copied = false;
  }
  area.remove();
  previous?.focus?.();
  return copied;
}

export function topicChips(topics) {
  if (!topics || !topics.length) return "";
  return `<div class="chips">${topics.map((topic) => `<span class="chip">${escapeHtml(topicLabel(topic))}</span>`).join("")}</div>`;
}

export function analysisStatus(status) {
  if (status === "pending") return '<span class="badge pending">анализ идёт</span>';
  if (status === "failed") return '<span class="badge failed">ошибка анализа</span>';
  if (status === "none") return '<span class="badge neutral">нет данных</span>';
  return '<span class="badge positive">готово</span>';
}

export function showError(element, message) {
  if (!element) return;
  element.textContent = message;
  element.hidden = false;
}

export function hideError(element) {
  if (!element) return;
  element.hidden = true;
  element.textContent = "";
}
