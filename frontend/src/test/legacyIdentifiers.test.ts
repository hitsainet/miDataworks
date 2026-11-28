// @vitest-environment node
// Feature 020 (FR-020.1; CF-2, CF-16, CF-25 m4/m5): the Vitest half of the export loader.
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

import { activeGuards, compileJs, entryOrWithheld, loadExport, parseExport, REPO_ROOT } from './legacyIdentifiers';

const header = { format: 1, repo: 'miDataworks', variant: 'full', register_hash: 'sha256:0', phase: 'P0', closed: null, withheld: [] };
const entry = {
  section: 'entry', id: 'DR-99', kind: 'k', group: 'C', status: 'frozen', retire_phase: null,
  pattern: '(?i)\\bx-millm-[a-z-]+', pattern_js: '\\bx-millm-[a-z-]+', js_flags: 'i',
  literals: ['X-miLLM-Strict'], exercise: null, flags: [],
};
const fileOf = (...records: object[]) => fileWith(header, ...records);
const fileWith = (head: object, ...records: object[]) => '```jsonl\n' + [head, ...records].map((r) => JSON.stringify(r)).join('\n') + '\n```\n';
const guard = { section: 'guard', id: 'G-99', effect: 'deny', pattern: 'x-old', pattern_js: 'x-old', js_flags: '', from_phase: 'P5', until_phase: null };

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

  // ENH-011 N1: phaseIndex answered -1 for an unknown phase, and parseExport never read header.phase, so
  // activeGuards silently dropped every from_phase guard (or kept an until_phase one it should drop).
  // The miWorkbench and miForge twins throw; so does this loader now, naming the phase.
  it('an unknown phase in the header fails the parse, naming it', () => {
    expect(() => parseExport(fileWith({ ...header, phase: 'P10' }))).toThrow(/phase P10/);
    expect(() => parseExport(fileWith({ ...header, phase: null }))).toThrow(/phase null/);
    expect(() => parseExport(fileWith({ ...header, phase: 'P5', closed: 'P10' }))).toThrow(/closed P10/);
    expect(parseExport(fileWith({ ...header, phase: 'P5', closed: null })).header.closed).toBeNull();
    expect(parseExport(fileWith({ ...header, phase: 'P9', closed: 'P9' })).header.phase).toBe('P9');
  });

  it("a guard's unknown from_phase or until_phase fails the parse and the guard selection", () => {
    expect(() => parseExport(fileOf({ ...guard, from_phase: 'P10' }))).toThrow(/G-99: from_phase P10/);
    expect(() => parseExport(fileOf({ ...guard, from_phase: null, until_phase: 'P10' }))).toThrow(/G-99: until_phase P10/);
    // activeGuards over a record list the parse never saw (a hand-built export) refuses too.
    const exp = parseExport(fileWith({ ...header, phase: 'P6' }, guard));
    expect(activeGuards(exp).map((g) => g.id)).toEqual(['G-99']);
    const hand = { ...exp, records: [{ ...guard, until_phase: 'Q1' }] };
    expect(() => activeGuards(hand)).toThrow(/phase Q1/);
    expect(() => activeGuards({ ...exp, header: { ...exp.header, phase: 'P10' } })).toThrow(/phase P10/);
  });

  it('from_phase and until_phase select by phase order', () => {
    const at = (phase: string) => activeGuards(parseExport(fileWith({ ...header, phase }, guard, { ...guard, id: 'G-98', from_phase: null, until_phase: 'P5' }))).map((g) => g.id);
    expect(at('P4')).toEqual(['G-98']);
    expect(at('P5')).toEqual(['G-99']);
    expect(at('P9')).toEqual(['G-99']);
  });
});
