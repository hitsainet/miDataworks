// Where the public miDataworks manual lives (manual/, published from the public mirror to GitHub
// Pages). The Help screen and every per-step Help link point into it; the in-app text stays a
// summary. This is the ONE place the address is written: set VITE_MANUAL_BASE_URL at build time to
// point at another copy (a self-hosted build of manual/, for example). manualLinks.test.tsx checks
// the default agrees with manual/docusaurus.config.ts (url + baseUrl) and that every page and
// anchor a topic names exists in the manual's sources.

export const DEFAULT_MANUAL_BASE_URL = 'https://hitsainet.github.io/miDataworks/';

/** The manual's base URL, always ending in "/". An empty or missing value takes the default. */
export function manualBaseUrl(raw: string | undefined = import.meta.env.VITE_MANUAL_BASE_URL): string {
  const value = (raw ?? '').trim() || DEFAULT_MANUAL_BASE_URL;
  return value.endsWith('/') ? value : `${value}/`;
}

export const MANUAL_BASE_URL = manualBaseUrl();

/** A manual page, e.g. "guide/labeling#endpoint-roles", as an absolute URL. */
export function manualUrl(page: string, base: string = MANUAL_BASE_URL): string {
  return `${base}${page.replace(/^\/+/, '')}`;
}
