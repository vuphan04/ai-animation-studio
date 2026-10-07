export type Lang = 'vi' | 'en'
const KEY = 'studio_lang'

function saved(): Lang {
  try { return localStorage.getItem(KEY) === 'en' ? 'en' : 'vi' } catch { return 'vi' }
}

/** The interface language of this page load. It is fixed until the page reloads, so constants can hold translated text. */
export const lang: Lang = saved()
document.documentElement.lang = lang
let table: Record<string, string> | null = null

/** Loads the English table when it is needed. The app starts after this, so module constants can call t(). */
export async function loadLanguage() {
  if (lang === 'en') table = (await import('./en')).en
}

/** Text in the interface language. The source text is Vietnamese; `{0}`, `{1}`… are filled from `args`. */
export function t(text: string, ...args: unknown[]): string {
  let out = text
  if (table) {
    // Spaces around a fragment belong to the layout, not to the entry in the table.
    const start = text.length - text.trimStart().length, end = text.trimEnd().length
    const found = table[text.slice(start, end)]
    if (found !== undefined) out = text.slice(0, start) + found + text.slice(end)
  }
  return args.length ? out.replace(/\{(\d+)\}/g, (_, index) => String(args[Number(index)])) : out
}

/** Saves the choice and reloads the page, so every screen is built again in the new language. */
export function setLanguage(next: Lang) {
  if (next === lang) return
  try { localStorage.setItem(KEY, next) } catch { return }
  window.location.reload()
}
