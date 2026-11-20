const MONTHS = [
  "январь", "февраль", "март", "апрель", "май", "июнь",
  "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
];

export async function apiGet(path) {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    cache: "no-store",
  });
  if (response.status === 401) {
    throw new Error("Требуется авторизация. Обновите страницу и введите логин и пароль.");
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
    throw new Error(message);
  }
  return response.json();
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
  Chart.defaults.color = "#86868b";
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

export const RULE_LABELS = {
  R1: "оценка vs текст",
  R2: "стажёр vs наставник",
  R3: "динамика",
  R4: "пропуск опроса",
  R5: "повтор проблемы",
  R6: "низкая оценка",
};

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

export function flagChip(flag, withTitle = true) {
  const severity = flag.severity || "low";
  const label = SEVERITY_LABELS[severity] || severity;
  const title = withTitle ? ` title="${escapeHtml(flag.explanation || "")}"` : "";
  return `<span class="badge ${escapeHtml(severity)}"${title}>${escapeHtml(flag.rule_code)} · ${escapeHtml(label)}</span>`;
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
