export function normalizeLocale(value, fallback = "en") {
  if (!value) return fallback;
  const lower = String(value).trim().toLowerCase();
  if (lower.startsWith("es")) return "es";
  return fallback;
}

export function t(stringsByLocale, locale, key, fallback = "") {
  const table = stringsByLocale?.[locale] || stringsByLocale?.en || {};
  if (Object.prototype.hasOwnProperty.call(table, key)) return table[key];
  const english = stringsByLocale?.en || {};
  if (Object.prototype.hasOwnProperty.call(english, key)) return english[key];
  return fallback || key;
}

export function formatTemplate(str, vars = {}) {
  return String(str || "").replaceAll(/\{(\w+)\}/g, (match, key) =>
    Object.prototype.hasOwnProperty.call(vars, key) ? String(vars[key]) : match,
  );
}

export function applyI18n(stringsByLocale, locale, root = document) {
  const table = stringsByLocale?.[locale] || stringsByLocale?.en || {};
  const english = stringsByLocale?.en || table;

  if (root?.documentElement) {
    root.documentElement.lang = locale;
  }

  root.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    const attr = el.getAttribute("data-i18n-attr");
    if (!key) return;

    const value = Object.prototype.hasOwnProperty.call(table, key)
      ? table[key]
      : english?.[key];
    if (value === undefined) return;

    if (attr) {
      attr.split(",").forEach((attrNameRaw) => {
        const attrName = attrNameRaw.trim();
        if (!attrName) return;
        if (attrName === "text") {
          el.textContent = value;
        } else {
          el.setAttribute(attrName, value);
        }
      });
      return;
    }

    el.textContent = value;
  });
}

