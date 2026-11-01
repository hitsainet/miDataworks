// Copy rules (R-03.62, handoff section 5; task 12.11): no button says "Submit" or "OK"; every page
// title is sentence case. Reads the source of every component, so a new screen is covered the day
// it is added.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { PANELS } from '@/config/panels';

const SRC = join(__dirname, '..');
const BANNED = /^(submit|ok)$/i;
/** Product names and acronyms that keep their capitals inside a sentence-case title. */
const PROPER = new Set(['miDataworks', 'miLLM', 'miStudio', 'miForge', 'Hugging', 'Face', 'Hub', 'MCP', 'TEI', 'TRL', 'AUROC']);

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [path] : [];
  });
}

/** The visible text of every <button>/<Button> element: children only, tags stripped. */
export function buttonLabels(source: string): string[] {
  const labels: string[] = [];
  // Attribute values may hold `{() => x}`: a plain [^>]* would stop at the arrow's `>` and the
  // label would never be read (control F06 survived exactly that way).
  const re = /<(button|Button)\b(?:[^>{]|\{(?:[^{}]|\{[^{}]*\})*\})*>([\s\S]*?)<\/\1>/g;
  for (const match of source.matchAll(re)) {
    const text = match[2]
      .replace(/<[^>]+>/g, ' ')
      .replace(/\{[^}]*\}/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    if (text) labels.push(text);
  }
  return labels;
}

export function isSentenceCase(title: string): boolean {
  const words = title.split(/\s+/).map((w) => w.replace(/^[(]|[)]$/g, ''));
  if (!/^[A-Z]/.test(words[0]) && !PROPER.has(words[0])) return false;
  return words.slice(1).every((w) => PROPER.has(w) || w === w.toLowerCase());
}

describe('copy audit', () => {
  const files = sources(SRC);

  it('reads the component sources', () => {
    expect(files.length).toBeGreaterThan(10);
    expect(files.some((f) => f.endsWith('SettingsPanel.tsx'))).toBe(true);
  });

  it('bans "Submit" and "OK" as button labels', () => {
    const offenders = files.flatMap((f) =>
      buttonLabels(readFileSync(f, 'utf8'))
        .filter((label) => BANNED.test(label))
        .map((label) => `${f}: ${label}`),
    );
    expect(offenders).toEqual([]);
  });

  it('finds the labels it audits (the scanner can see a banned label)', () => {
    expect(buttonLabels('<Button onClick={x}>OK</Button>')).toEqual(['OK']);
    expect(buttonLabels('<button type="button">\n  Submit\n</button>')).toEqual(['Submit']);
    expect(buttonLabels('<Button>Save the {role} endpoint</Button>')).toEqual(['Save the endpoint']);
    expect(buttonLabels("<Button onClick={() => void save('x', { a: 1 })}>OK</Button>")).toEqual(['OK']);
  });

  it('every page title is sentence case', () => {
    for (const panel of PANELS) expect(isSentenceCase(panel.title), panel.title).toBe(true);
    expect(isSentenceCase('Publish And Export')).toBe(false);
    expect(isSentenceCase('Send to miStudio')).toBe(true);
  });

  it('every h1 and h2 written in a component is sentence case', () => {
    const offenders: string[] = [];
    for (const f of files) {
      for (const m of readFileSync(f, 'utf8').matchAll(/<h[12]\b(?:[^>{]|\{[^{}]*\})*>([^<{]+)</g)) {
        const text = m[1].trim();
        if (text && !isSentenceCase(text)) offenders.push(`${f}: ${text}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
