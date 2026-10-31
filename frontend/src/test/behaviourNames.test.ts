/**
 * No shipped frontend code names a behaviour (FR-016.33; 016 FTASKS 5.4).
 *
 * The TypeScript compiler parses every SHIPPED file - those not matched by Vitest's `test.include`
 * (read by importing vite.config.ts) and not under src/test/ - and reads identifiers, string
 * literals, template text and JSX text; comments are never read. Names come from behaviour data
 * only (backend/tests/fixtures/behaviours/names.json, written by the backend's
 * scripts/write_behaviour_names.py); hits listed in name_debt.json are allowed, and a listed
 * frontend entry whose token is gone is stale. The source fails closed when empty.
 */
import { readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import viteConfig from '../../vite.config';

const FRONTEND = path.resolve(import.meta.dirname, '../..');
const REPO = path.resolve(FRONTEND, '..');
const BEHAVIOURS = path.join(REPO, 'backend/tests/fixtures/behaviours');

type Hit = { file: string; name: string; text: string };

export function words(text: string): string[] {
  const out: string[] = [];
  for (const chunk of text.match(/[A-Za-z0-9]+/g) ?? []) {
    for (const w of chunk.match(/[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+/g) ?? []) out.push(w.toLowerCase());
  }
  return out;
}

function contains(hay: string[], needle: string[]): boolean {
  if (needle.length === 0) return false;
  for (let i = 0; i + needle.length <= hay.length; i += 1) {
    if (needle.every((w, j) => hay[i + j] === w)) return true;
  }
  return false;
}

export function loadNames(file = path.join(BEHAVIOURS, 'names.json')): Map<string, string[]> {
  const data = JSON.parse(readFileSync(file, 'utf8')) as {
    names: string[];
    name_count: number;
    behaviours: { behaviour_key: string; vocabulary: string[] }[];
  };
  const declared = new Set(data.behaviours.flatMap((b) => [b.behaviour_key, ...b.vocabulary]));
  if (data.names.length === 0) throw new Error('the name source is empty: the guard would check nothing');
  if (data.names.length < declared.size || data.name_count !== data.names.length) {
    throw new Error('the name source holds fewer names than the behaviour data declares');
  }
  return new Map(data.names.map((n) => [n, words(n)]));
}

function globToRegExp(glob: string): RegExp {
  let re = '';
  for (let i = 0; i < glob.length; i += 1) {
    const c = glob[i];
    if (c === '*' && glob[i + 1] === '*') {
      re += '(?:.*/)?';
      i += glob[i + 2] === '/' ? 2 : 1;
    } else if (c === '*') re += '[^/]*';
    else if (c === '{') {
      const end = glob.indexOf('}', i);
      re += `(?:${glob.slice(i + 1, end).split(',').join('|')})`;
      i = end;
    } else re += /[.+^$()|[\]\\]/.test(c) ? `\\${c}` : c;
  }
  return new RegExp(`^${re}$`);
}

function testIncludes(): RegExp[] {
  const config = typeof viteConfig === 'function' ? viteConfig({ mode: 'test', command: 'serve' }) : viteConfig;
  const include = (config as { test?: { include?: string[] } }).test?.include ?? [];
  if (include.length === 0) throw new Error('vite.config.ts declares no test.include');
  return include.map(globToRegExp);
}

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

export function shippedFiles(root = FRONTEND): string[] {
  const includes = testIncludes();
  return walk(path.join(root, 'src'))
    .filter((f) => /\.(ts|tsx)$/.test(f))
    .map((f) => path.relative(root, f).split(path.sep).join('/'))
    .filter((rel) => !rel.startsWith('src/test/') && !includes.some((re) => re.test(rel)));
}

export function tokens(source: string, fileName: string): string[] {
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, fileName.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out: string[] = [];
  const visit = (node: ts.Node): void => {
    if (
      ts.isIdentifier(node) ||
      ts.isPrivateIdentifier(node) ||
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node) ||
      ts.isJsxText(node)
    ) {
      out.push(ts.isIdentifier(node) || ts.isPrivateIdentifier(node) ? node.text : (node as ts.LiteralLikeNode).text);
    }
    node.forEachChild(visit);
  };
  visit(sf);
  return out;
}

export function hits(names: Map<string, string[]>, files: [string, string][]): Hit[] {
  const found: Hit[] = [];
  for (const [file, source] of files) {
    for (const text of tokens(source, file)) {
      const w = words(text);
      for (const [name, seq] of names) if (contains(w, seq)) found.push({ file, name, text: text.slice(0, 80) });
    }
  }
  return found;
}

type Debt = { path: string; token: string; owner: string; reason: string };
const debt = (JSON.parse(readFileSync(path.join(BEHAVIOURS, 'name_debt.json'), 'utf8')) as { entries: Debt[] }).entries;

function read(rel: string): [string, string] {
  return [rel, readFileSync(path.join(FRONTEND, rel), 'utf8')];
}

describe('behaviour-name guard (TypeScript)', () => {
  it('finds no behaviour name in a shipped file', () => {
    const files = shippedFiles();
    expect(files.length).toBeGreaterThan(50);
    expect(files.some((f) => f.endsWith('.test.tsx'))).toBe(false);
    const listed = new Set(debt.map((d) => `${d.path}|${d.token.toLowerCase()}`));
    const found = hits(loadNames(), files.map(read)).filter((h) => !listed.has(`frontend/${h.file}|${h.name.toLowerCase()}`));
    expect(found).toEqual([]);
  });

  it('has no stale frontend debt', () => {
    const stale = debt
      .filter((d) => d.path.startsWith('frontend/'))
      .filter((d) => !tokens(readFileSync(path.join(REPO, d.path), 'utf8'), d.path).some((t) => contains(words(t), words(d.token))));
    expect(stale).toEqual([]);
  });

  it('reads identifiers, strings and JSX text but not comments', () => {
    const found = tokens('// zantimere\nconst quorbishCount = 1;\nconst s = "Fixture-Alpha";\nconst j = <b>plovish</b>;', 'x.tsx');
    expect(found).toContain('quorbishCount');
    expect(found).toContain('Fixture-Alpha');
    expect(found.join(' ')).toContain('plovish');
    expect(found.join(' ')).not.toContain('zantimere');
  });

  // The four negative controls (FTASKS 5.7), in-process; the file-edit controls are recorded too.
  it('control 1: a behaviour key in a string literal of a shipped module is red', () => {
    expect(hits(loadNames(), [['src/api/x.ts', 'export const a = "fixture-alpha";']]).map((h) => h.name)).toEqual(['fixture-alpha']);
  });

  it('control 2: the same key in a validator-related module is red (no exemption in the frontend)', () => {
    expect(hits(loadNames(), [['src/components/rules/validators.ts', 'export const v = "fixture-alpha";']]).length).toBe(1);
  });

  it('control 3: a vocabulary term as an identifier is red', () => {
    expect(hits(loadNames(), [['src/x.ts', 'export const quorbishRows = 2;']]).map((h) => h.name)).toEqual(['quorbish']);
  });

  it('control 4: an empty name source is red', () => {
    const tmp = path.join(FRONTEND, 'node_modules', '.empty-names.json');
    writeFileSync(tmp, JSON.stringify({ behaviours: [], names: [], name_count: 0 }));
    try {
      expect(() => loadNames(tmp)).toThrow(/empty/);
    } finally {
      rmSync(tmp);
    }
  });
});
