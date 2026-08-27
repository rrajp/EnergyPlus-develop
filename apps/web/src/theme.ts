/**
 * Theme resolution, kept out of any component.
 *
 * The toggle lives inside the authenticated shell, so if it owned this the
 * login screen would ignore both the stored preference and `?theme=` and
 * render in whatever the OS happens to prefer.
 */

export type Theme = 'system' | 'light' | 'dark'

const KEY = 'pw-theme'

const isTheme = (v: string | null): v is Theme =>
  v === 'system' || v === 'light' || v === 'dark'

/** URL wins, then the stored preference, then the OS. */
export function readTheme(): Theme {
  const q = new URLSearchParams(location.search).get('theme')
  if (isTheme(q)) return q
  try {
    const stored = localStorage.getItem(KEY)
    if (isTheme(stored)) return stored
  } catch {
    // Private browsing can throw on access; the system default is fine.
  }
  return 'system'
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement
  if (theme === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', theme)
}

export function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(KEY, theme)
  } catch {
    // Not being able to remember the choice is not worth an error.
  }
}
