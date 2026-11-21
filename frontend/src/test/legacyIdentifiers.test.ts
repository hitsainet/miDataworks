// @vitest-environment node
// Feature 020 (FR-020.1; CF-2, CF-16, CF-25 m4/m5): the Vitest half of the export loader.
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

import { compileJs, entryOrWithheld, loadExport, parseExport, REPO_ROOT } from './legacyIdentifiers';

const header = { format: 1, repo: 'miDataworks', variant: 'full', register_hash: 'sha256:0', phase: 'P0', closed: null, withheld: [] };
const entry = {
  section: 'entry', id: 'DR-99', kind: 'k', group: 'C', status: 'frozen', retire_phase: null,
  pattern: '(?i)\\bx-millm-[a-z-]+', pattern_js: '\\bx-millm-[a-z-]+', js_flags: 'i',
  literals: ['X-miLLM-Strict'], exercise: null, flags: [],
};
const fileOf = (...records: object[]) => '```jsonl\n' + [header, ...records].map((r) => JSON.stringify(r)).join('\n') + '\n```\n';

describe('the legacy-identifiers export (Vitest loader)', () => {
  it('parses the committed export', () => {
    const exp = loadExport();
    expect(exp.header.repo).toBe('miDataworks');
    expect(exp.records.some((r) => r.section === 'entry')).toBe(true);
  });

  it('every literal matches new RegExp(pattern_js, js_flags)', () => {
    const exp = loadExport();
    const checked = exp.records.filter((r) => r.section === 'entry').flatMap((r) => {
      const rx = compileJs(r as unknown as { pattern_js: string; js_flags: string });
      return ((r.literals as string[]) ?? []).map((lit) => [lit, rx.test(lit)] as const);
    });
    expect(checked.length).toBeGreaterThan(5);
    expect(checked.filter(([, ok]) => !ok)).toEqual([]);
    expect(() => parseExport(fileOf({ ...entry, pattern_js: '\\bx-miserver-[a-z-]+' }))).toThrow(/not matched by pattern_js/);
  });

  it('inline flag groups are refused but lookarounds pass', () => {
    expect(() => parseExport(fileOf({ ...entry, pattern_js: '(?i:x-millm-strict)', js_flags: '' }))).toThrow(/inline flag/);
    expect(() => parseExport(fileOf({ ...entry, pattern_js: '(?s)x-millm-strict' }))).toThrow(/inline flag/);
    for (const legal of ['(?<![-\\w])x-millm-strict(?![-\\w])', '(?:x-millm-strict|x-millm-seed)', '(?=x)x-millm-strict']) {
      expect(() => parseExport(fileOf({ ...entry, pattern_js: legal }))).not.toThrow();
    }
    expect(() => parseExport(fileOf({ ...entry, js_flags: 'g' }))).toThrow(/js_flags/);
  });

  it('falls back to the public file and skips withheld ids', () => {
    const root = mkdtempSync(path.join(tmpdir(), 'legacy-'));
    mkdirSync(path.join(root, 'docs'));
    copyFileSync(path.join(REPO_ROOT, 'docs', 'legacy-identifiers.public.md'), path.join(root, 'docs', 'legacy-identifiers.public.md'));
    const exp = loadExport(root);
    expect(exp.header.variant).toBe('public');
    const withheld = exp.header.withheld.filter((id) => id.startsWith('DR-'));
    expect(withheld.length).toBeGreaterThan(0);
    for (const id of withheld) expect(entryOrWithheld(exp, id)).toBe('withheld');
    expect(entryOrWithheld(exp, 'DR-08')).toMatchObject({ id: 'DR-08' });
    expect(readFileSync(path.join(root, 'docs', 'legacy-identifiers.public.md'), 'utf-8')).not.toMatch(/hitsai\.local/);
  });
});
