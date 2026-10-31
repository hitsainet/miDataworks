// @vitest-environment node
// R-03.59 and the mockup handoff's colour rules (0xcc/prds/midataworks-mockup-handoff.md, "Accent
// rules"): surfaces are slate; green, amber and red are status; indigo is the accent; cyan, emerald
// and orange are miLLM, miStudio and miForge state. No other colour family is drawn, and indigo is
// never the background of a notice. Audited 2026-10-21 (000 FTASKS 18.9): sky, purple and
// indigo-tinted notices were found and recoloured; each scan below carries a positive control so a
// pattern that stops matching fails instead of passing quietly.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = path.resolve(import.meta.dirname, '..');

function sources(dir: string = SRC): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) out.push(...sources(full));
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) && !full.includes(`${path.sep}test${path.sep}`)) out.push(full);
  }
  return out;
}

const OFF_PALETTE = /\b(?:bg|text|border|ring|from|to|via|fill|stroke|outline|divide|accent|decoration|shadow|placeholder|caret)-(sky|blue|purple|violet|fuchsia|pink|teal|lime|rose|yellow|gray|zinc|neutral|stone)-\d{2,3}\b/g;
// An opening JSX tag that is a notice (role status or alert) and paints an indigo background.
const INDIGO_NOTICE = /<\w+[^>]*role="(?:status|alert)"[^>]*\bbg-indigo-[^>]*>|<\w+[^>]*\bbg-indigo-[^>]*role="(?:status|alert)"[^>]*>/g;

describe('colour palette (R-03.59)', () => {
  const files = sources();

  it('scans the real source tree', () => {
    expect(files.length).toBeGreaterThan(100);
    expect(files.some((f) => f.endsWith(path.join('layout', 'Sidebar.tsx')))).toBe(true);
  });

  it('draws no colour family outside slate, status, indigo and the three app colours', () => {
    // Positive control: the pattern finds what it is meant to find.
    expect('x bg-sky-50 dark:text-purple-400 y'.match(OFF_PALETTE)).toEqual(['bg-sky-50', 'text-purple-400']);
    expect('bg-slate-50 text-indigo-700 bg-cyan-500/10'.match(OFF_PALETTE)).toBeNull();
    const found = files.flatMap((f) =>
      [...readFileSync(f, 'utf8').matchAll(OFF_PALETTE)].map((m) => `${path.relative(SRC, f)}: ${m[0]}`),
    );
    expect(found).toEqual([]);
  });

  it('never paints a notice indigo', () => {
    expect('<p role="status" className="rounded-control bg-indigo-50 px-2">'.match(INDIGO_NOTICE)).toHaveLength(1);
    expect('<div className="bg-indigo-50" role="alert">'.match(INDIGO_NOTICE)).toHaveLength(1);
    expect('<p role="status" className="bg-slate-50">'.match(INDIGO_NOTICE)).toBeNull();
    const found = files.flatMap((f) =>
      [...readFileSync(f, 'utf8').matchAll(INDIGO_NOTICE)].map((m) => `${path.relative(SRC, f)}: ${m[0].slice(0, 80)}`),
    );
    expect(found).toEqual([]);
  });
});
