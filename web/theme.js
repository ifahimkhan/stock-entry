// Light / dark theme switch. Light is the default; the choice is remembered in this browser only.
// index.html sets data-theme before the page paints (no flash); this module wires the header button.

const STORAGE_KEY = "stock-entry-theme";
const THEMES = ["light", "dark"];
const DEFAULT_THEME = "light";
const BUTTON_TEXT = { light: "☾", dark: "☀" };   // the icon shows the theme a click switches to
const BUTTON_LABEL = { light: "Switch to dark mode", dark: "Switch to light mode" };

/** The saved theme, or the default when nothing valid is saved (or storage is blocked). */
export function savedTheme() {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return THEMES.includes(value) ? value : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

function saveTheme(theme) {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // private window / blocked storage: the switch still works for this visit
  }
}

function applyTheme(theme, button) {
  document.documentElement.dataset.theme = theme;
  if (button) {
    button.textContent = BUTTON_TEXT[theme];
    button.setAttribute("aria-label", BUTTON_LABEL[theme]);
    button.title = BUTTON_LABEL[theme];
  }
}

/** Show the current theme on `button` and switch theme on click. */
export function initThemeToggle(button) {
  applyTheme(savedTheme(), button);
  if (!button) return;
  button.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next, button);
    saveTheme(next);
  });
}
