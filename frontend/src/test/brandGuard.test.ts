// @vitest-environment node
// Feature 020 (FR-020.11, FR-020.23, FR-020.24; CF-15, CF-20, CF-21): the frontend brand scanner.
// Read surfaces are the string text of src/**/*.{ts,tsx} (not tests, not src/test): string and
// no-substitution template literals, template parts, JSX text and JSX attribute strings, through
// the TypeScript compiler API. Comments, identifiers and literal types are not surfaces (PD-7).
// Allowances and phase rules come from the export only; brand-guard.json holds mode and deny.
// Report mode prints and counts; enforce mode (from P4's close) fails on any unallowed hit.
// Scope (FPRD Q4, PD-7): read text is enforced; an identifier token (a whole literal shaped like
// one, or the same inside a backtick code span) is reported only. `classifiedHits` makes the
// split; backend/tests/unit/brand/scope_cases.json holds it to the pytest loader's answers.
// Mode (suite decision R23): enforced in the private tree, report on the public mirror snapshot.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

import { appliedMode, classifiedHits, type Export, loadExport, mirrorSnapshot, REPO_ROOT, unallowedHits } from './legacyIdentifiers';

const POLICY = JSON.parse(readFileSync(path.join(REPO_ROOT, 'brand-guard.json'), 'utf-8')) as { mode: string; deny: string };

function sourceFiles(): string[] {
  const out = execFileSync(
    'git',
    ['ls-files', '-z', '--', 'frontend/src/*.ts', 'frontend/src/*.tsx', 'frontend/src/**/*.ts', 'frontend/src/**/*.tsx', ':(exclude,glob)**/docs/legacy-identifiers*.md'],
    { cwd: REPO_ROOT },
  ).toString('utf-8');
  return out
    .split('\0')
    .filter((f) => f && !/\.test\.tsx?$/.test(f) && !f.startsWith('frontend/src/test/'))
    .map((f) => path.join(REPO_ROOT, f));
}

/** Read surfaces through the AST. */
export function surfaceTexts(file: string, source = readFileSync(file, 'utf-8')): string[] {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isLiteralTypeNode(node)) return; // a literal type is not a surface
    if (ts.isPropertyAssignment(node) && (ts.isStringLiteral(node.name) || ts.isIdentifier(node.name))) {
      visit(node.initializer); // an object key is not a surface; its value is
      return;
    }
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) out.push(node.text);
    else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) out.push(node.text);
    else if (ts.isJsxText(node)) out.push(node.text);
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

function count(exp: Export, texts: string[]): number {
  return texts.reduce((n, t) => n + classifiedHits(t, exp, POLICY.deny).read.length, 0);
}

const readHits = (exp: Export, texts: string[]): string[] => texts.flatMap((t) => classifiedHits(t, exp, POLICY.deny).read);

/** A scratch repository shaped like the mirror snapshot (one parentless commit with the sync's
 * trailers) or like the private tree (two plain commits). */
function scratchRepo(shape: 'mirror' | 'private'): string {
  const root = mkdtempSync(path.join(tmpdir(), `brand-${shape}-`));
  const git = (...args: string[]) => execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args], { cwd: root, stdio: 'ignore' });
  git('init', '-q');
  writeFileSync(path.join(root, 'README.md'), 'x\n');
  git('add', '-A');
  if (shape === 'private') {
    git('commit', '-qm', 'first');
    writeFileSync(path.join(root, 'README.md'), 'y\n');
    git('add', '-A');
    git('commit', '-qm', 'second');
  } else {
    git('commit', '-qm', 'miDataworks - filtered snapshot', '--trailer', 'Source-Commit: 6193435066ff3a9ac7c387832953ec4273cdfda4', '--trailer', 'Private-CI: passed');
  }
  return root;
}

describe('brand guard: frontend read surfaces', () => {
  const exp = loadExport();
  const SLOW = 60_000; // two full reads of src through the compiler; the default 5 s is too tight on a busy runner

  it('report count equals the reference grep', () => {
    const files = sourceFiles();
    expect(files.length).toBeGreaterThan(100);
    const all = files.flatMap((f) => surfaceTexts(f));
    const astCount = count(exp, all);
    const identifiers = all.reduce((n, t) => n + classifiedHits(t, exp, POLICY.deny).identifier.length, 0);
    // Reference, an independent read of the same files: the whole text, line by line, comments
    // included, searched for the old names in their display forms (case-sensitive: these can
    // never be a key). The tree read above is a subset of it, so zero here bounds it from above.
    const display = '(miLLM|miStudio)';
    // The deny guards deny in any case; this read is of the display forms alone. The ALLOW guards
    // stay: from P5 a comment may name the suite by PD-19's phrases (brand.ts:35, "the miStudio
    // suite"; FTASKS 18.2), which the rules allow and the reference must allow too.
    const entriesOnly: Export = { ...exp, records: exp.records.filter((r) => r.section !== 'guard' || r.effect === 'allow') };
    const reference: string[] = [];
    for (const f of files) {
      readFileSync(f, 'utf-8').split('\n').forEach((line, i) => {
        for (const h of unallowedHits(line, entriesOnly, display)) reference.push(`${path.relative(REPO_ROOT, f)}:${i + 1}: ${h}`);
      });
    }
    console.warn(`brand-guard[frontend] ${appliedMode(POLICY.mode)} mode: ${astCount} unallowed on read surfaces (display-form reference over whole files: ${reference.length}); ${identifiers} identifier hit(s), reported only`);
    // From P4's copy pass the read surfaces hold no old name; the identifiers the app still
    // carries (frozen keys such as the health block's) are counted apart and stay.
    if (mirrorSnapshot()) return; // the public export withholds allowances (R23): counted, not judged
    expect(astCount).toBe(0);
    expect(reference).toEqual([]);
    expect(identifiers).toBeGreaterThan(0);
  }, SLOW);

  it('enforce mode fails on an unallowed hit', () => {
    const hits = readHits(exp, sourceFiles().flatMap((f) => surfaceTexts(f)));
    const mode = appliedMode(POLICY.mode);
    if (mode === 'enforce') expect(hits).toEqual([]);
    else expect(mode).toBe('report');
    const planted = surfaceTexts('x.tsx', 'export const A = () => <p title="Open miLLM">Load it in miLLM</p>;');
    expect(readHits(exp, planted)).toEqual(['miLLM', 'miLLM']);
    expect(surfaceTexts('y.ts', "type K = 'millm'; const o = { millm: 1 };")).toEqual([]);
  }, SLOW);

  it('the tree is ready for enforce: no old name on a read surface (the P4 dry run)', () => {
    // FR-020.23: the last P4 push proves zero findings before the mode flips. Not keyed to the
    // mode, so it is red on the commit that adds an old name, in report mode too. On the mirror
    // snapshot the public export withholds DR-29's host names; the private CI holds this verdict.
    const p4: Export = { ...exp, header: { ...exp.header, phase: 'P4', closed: 'P4' } };
    const found: string[] = [];
    for (const f of sourceFiles()) for (const t of surfaceTexts(f)) for (const h of classifiedHits(t, p4, POLICY.deny).read) found.push(`${path.relative(REPO_ROOT, f)}: ${h} in ${JSON.stringify(t.slice(0, 80))}`);
    if (mirrorSnapshot()) console.warn(`brand-guard[frontend] mirror snapshot: ${found.length} hit(s) under the public export, reported only (R23)`);
    else expect(found).toEqual([]);
  }, SLOW);

  it('a visible label naming an old app is read; a frozen key is an identifier', () => {
    const jsx = surfaceTexts('z.tsx', 'export const A = () => <AppChip app="millm" label="miStudio" title="Open miLLM">miStudio</AppChip>;');
    expect(jsx).toEqual(['millm', 'miStudio', 'Open miLLM', 'miStudio']);
    expect(readHits(exp, jsx)).toEqual(['miStudio', 'miLLM', 'miStudio']);
    expect(classifiedHits('millm', exp, POLICY.deny)).toEqual({ read: [], identifier: ['millm'] });
  });

  it('the scope rule gives the same answers as the pytest loader', () => {
    const table = JSON.parse(readFileSync(path.join(REPO_ROOT, 'backend/tests/unit/brand/scope_cases.json'), 'utf-8')) as {
      cases: { text: string; whole: boolean; spans: boolean; read: string[]; identifier: string[] }[];
    };
    expect(table.cases.length).toBeGreaterThanOrEqual(25);
    for (const c of table.cases) {
      const got = classifiedHits(c.text, exp, POLICY.deny, { whole: c.whole, codeSpans: c.spans });
      expect({ text: c.text, ...got }).toEqual({ text: c.text, read: c.read, identifier: c.identifier });
      expect([...got.read, ...got.identifier].sort()).toEqual([...unallowedHits(c.text, exp, POLICY.deny)].sort());
    }
  });

  it('enforce applies in the private tree and report on the mirror snapshot (R23)', () => {
    const mirror = scratchRepo('mirror');
    const priv = scratchRepo('private');
    expect(mirrorSnapshot(mirror)).toBe(true);
    expect(mirrorSnapshot(priv)).toBe(false);
    expect(mirrorSnapshot(tmpdir())).toBe(false);
    expect(appliedMode('enforce', priv)).toBe('enforce');
    expect(appliedMode('enforce', mirror)).toBe('report');
    expect(appliedMode('report', priv)).toBe('report');
    expect(appliedMode('report', mirror)).toBe('report');
  });

  it('a host record in transition allows its old address, a retired one nothing', () => {
    const line = 'http://millm-backend.millm.svc.cluster.local:8000';
    const withHost = (status: string): Export => ({
      ...exp,
      records: [...exp.records.filter((r) => r.section !== 'host'), { section: 'host', old: 'millm-backend.millm.svc.cluster.local', new: 'miserver-backend.miserver.svc.cluster.local', status, observed: true }],
    });
    expect(unallowedHits(line, withHost('transition'), POLICY.deny)).toEqual([]);
    expect(unallowedHits(line, withHost('retired'), POLICY.deny)).toEqual(['millm', 'millm']);
    expect(unallowedHits('millm-backend.millm.example', withHost('transition'), POLICY.deny)).not.toEqual([]);
  });

  it('suite_phrase P4 and P5 cases', () => {
    const at = (phase: string, closed: string): Export => ({ ...exp, header: { ...exp.header, phase, closed } });
    const deny = POLICY.deny;
    expect(unallowedHits('Open miStudio to train it.', at('P4', 'P3'), deny)).toEqual(['miStudio']);
    expect(unallowedHits('part of the miStudio suite', at('P4', 'P3'), deny)).toEqual(['miStudio']);
    expect(unallowedHits('see the miSuite tracker', at('P4', 'P3'), deny)).toEqual([]);
    expect(unallowedHits('part of the miStudio suite', at('P5', 'P4'), deny)).toEqual([]);
    expect(unallowedHits('see the miSuite tracker', at('P5', 'P4'), deny)).toEqual(['miSuite']);
    // G-STUDIO-PATH is withheld from the public variant (a home path); the mirror skips this case by name.
    if (exp.records.some((r) => r.section === 'guard' && r.id === 'G-STUDIO-PATH')) {
      expect(unallowedHits('cd ~/app/miStudio/x', at('P5', 'P4'), deny)).toEqual([]);
    } else {
      expect(exp.header.withheld).toContain('G-STUDIO-PATH');
    }
  });
});
