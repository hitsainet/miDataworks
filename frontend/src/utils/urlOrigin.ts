// The origin a role's API key is bound to: scheme, host and port, with the default port explicit.
// Mirrors the backend's src/core/key_binding.py url_origin (urlOrigin.test.ts pins shared cases),
// so the Settings card can warn BEFORE a save that a new server will clear the stored key.
const DEFAULT_PORTS: Record<string, string> = { 'http:': '80', 'https:': '443' };

export function urlOrigin(url: string | null | undefined): string | null {
  if (!url || !url.trim()) return null;
  let parsed: URL;
  try {
    parsed = new URL(url.trim());
  } catch {
    return null;
  }
  const port = parsed.port || DEFAULT_PORTS[parsed.protocol];
  if (!port || !parsed.hostname) return null;
  return `${parsed.protocol}//${parsed.hostname.toLowerCase()}:${port}`;
}
