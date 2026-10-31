// @vitest-environment node
// Corners are two named sizes (operator decision 2026-10-31): rounded-card and rounded-control, the
// --radius-card / --radius-control tokens in src/index.css. Pills, badges, dots and progress bars
// are rounded-full; rounded-none stays where it is deliberate. This reads every source file from
// disk (tests and test support excluded) and refuses any other corner class, wherever it is
// written: a className string, a template literal, an array of class fragments or a CSS @apply.
// A raw size (rounded-lg, rounded-sm, rounded, rounded-[6px]) would bypass the two tokens, so
// changing the look by editing two numbers would stop being true.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(import.meta.dirname, '../..');
const SRC = path.join(ROOT, 'src');

const ALLOWED = 'rounded[-side]-(card|control|full|none), side one of t r b l tl tr br bl s e ss se es ee';
const SIDE = '(?:t|r|b|l|tl|tr|br|bl|s|e|ss|se|es|ee)';
const OK = new RegExp(`^rounded(?:-${SIDE})?-(?:card|control|full|none)$`);
// Any token that starts with `rounded`: bare, a size, an arbitrary value, a CSS-variable value or
// an interpolated `${...}` fragment. Variant prefixes (dark:, sm:, hover:) and the important mark
// end at the `:` or `!` and do not change the check.
const TOKEN = /(?<![A-Za-z0-9_-])rounded(?:-\$\{[^}]*\}|-[A-Za-z0-9_.[\]()%#/-]*)?/g;

/** Every corner class in `text` that is not one of the allowed four, with its 1-based line. */
function cornerOffenders(text: string): { line: number; token: string }[] {
  const out: { line: number; token: string }[] = [];
  text.split('\n').forEach((line, i) => {
    for (const m of line.matchAll(TOKEN)) {
      const token = m[0].replace(/[-/.]+$/, '');
      if (!OK.test(token)) out.push({ line: i + 1, token: m[0] });
    }
  });
  return out;
}

function sourceFiles(): string[] {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = path.join(dir, name);
      if (statSync(p).isDirectory()) {
        if (p !== path.join(SRC, 'test')) walk(p);
      } else if (/\.(tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name)) files.push(p);
    }
  };
  walk(SRC);
  return files;
}

describe('corner classes', () => {
  it('finds a raw size in every place a class can be written (positive control)', () => {
    const planted = [
      '<div className="p-4 rounded-lg border">',
      'const btn = `px-3 ${x ? "rounded-sm" : ""}`;',
      "const parts = ['flex', 'rounded-md', 'gap-2'];",
      '<span className="rounded px-1">',
      '<i className="md:rounded-xl dark:rounded-t-lg">',
      '<b className="rounded-[6px] rounded-(--r)">',
      'const r = `rounded-${size}`;',
      '  @apply rounded-2xl;',
    ].join('\n');
    expect(cornerOffenders(planted)).toEqual([
      { line: 1, token: 'rounded-lg' },
      { line: 2, token: 'rounded-sm' },
      { line: 3, token: 'rounded-md' },
      { line: 4, token: 'rounded' },
      { line: 5, token: 'rounded-xl' },
      { line: 5, token: 'rounded-t-lg' },
      { line: 6, token: 'rounded-[6px]' },
      { line: 6, token: 'rounded-(--r)' },
      { line: 7, token: 'rounded-${size}' },
      { line: 8, token: 'rounded-2xl' },
    ]);
  });

  it('lets the allowed set through, with variants and sides', () => {
    const fine =
      'rounded-card rounded-control rounded-full rounded-none rounded-t-card rounded-bl-control ' +
      'hover:rounded-control sm:rounded-r-card `rounded-card ${a}` [\'rounded-full\'] Math.round(x)';
    expect(cornerOffenders(fine)).toEqual([]);
  });

  it('uses only rounded-card, rounded-control, rounded-full and rounded-none in src/', () => {
    const files = sourceFiles();
    expect(files.length).toBeGreaterThan(100);
    // The walk reaches the components that carry the two tokens (an empty walk passes vacuously).
    const text = files.map((f) => readFileSync(f, 'utf8')).join('\n');
    expect(text).toContain('rounded-card');
    expect(text).toContain('rounded-control');
    const found = files.flatMap((f) =>
      cornerOffenders(readFileSync(f, 'utf8')).map(
        ({ line, token }) => `${path.relative(ROOT, f)}:${line} ${token}`,
      ),
    );
    expect(found, `corner classes outside the allowed set (${ALLOWED}):\n${found.join('\n')}`).toEqual([]);
  });
});
