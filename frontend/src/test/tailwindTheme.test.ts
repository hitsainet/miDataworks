// @vitest-environment node
// Tailwind CSS 4 keeps the app looking as it did on Tailwind CSS 3.4 (src/index.css). This compiles
// the real src/index.css through the real PostCSS plugin and pins each v3 behaviour the file
// restores, so deleting or changing one of those blocks turns a test red. The before/after
// screenshot comparison of every screen is the wider check; these are the pieces a test can hold.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import postcss from 'postcss';
import { beforeAll, describe, expect, it } from 'vitest';

import { tokens } from '@/config/brand';

const ROOT = path.resolve(import.meta.dirname, '../..');
const SRC = path.join(ROOT, 'src');

// Utilities the pins below need, generated whether or not a component uses them today.
const PROBES = [
  'stack-y-2',
  'w-sidebar',
  'w-sidebar-collapsed',
  'h-topbar',
  'text-sm',
  'text-xs',
  'hover:bg-indigo-600',
  'dark:bg-slate-950',
  'bg-green-500/15',
  'font-sans',
  'font-mono',
];

let css = '';
let theme: Record<string, string> = {};

beforeAll(async () => {
  const input = `@import './index.css';\n@source inline(${JSON.stringify(PROBES.join(' '))});\n`;
  // The plugins postcss.config.js names, loaded as Vite loads them, so the app's own PostCSS
  // wiring is what compiles here (an empty or v3-style config leaves the utilities ungenerated).
  const config = (await import(path.join(ROOT, 'postcss.config.js'))) as {
    default: { plugins: Record<string, object> };
  };
  const plugins = await Promise.all(
    Object.entries(config.default.plugins).map(async ([name, opts]) => {
      const mod = (await import(name)) as { default: (o: object) => postcss.AcceptedPlugin };
      return mod.default({ ...opts, base: ROOT, optimize: false });
    }),
  );
  const result = await postcss(plugins).process(input, {
    from: path.join(SRC, '__tailwindTheme.test.css'),
  });
  css = result.css;
  // The last declaration of each theme variable wins, as in the browser.
  theme = {};
  for (const m of css.matchAll(/(--[\w-]+):\s*([^;]+);/g)) theme[m[1]] = m[2].trim();
}, 60_000);

/** The CSS rule bodies whose selector contains `selector`, joined. */
function rulesFor(selector: string): string {
  const out: string[] = [];
  let i = css.indexOf(selector);
  while (i !== -1) {
    const open = css.indexOf('{', i);
    const close = css.indexOf('}', open);
    out.push(css.slice(open + 1, close));
    i = css.indexOf(selector, close);
  }
  return out.join('\n');
}

describe('theme values carried from Tailwind CSS 3', () => {
  it('keeps the v3 hex palette, not v4 OKLCH', () => {
    expect(theme['--color-indigo-500']).toBe('#6366f1');
    expect(theme['--color-green-500']).toBe('#22c55e');
    expect(theme['--color-slate-900']).toBe('#0f172a');
    expect(
      Object.entries(theme).filter(([k, v]) => k.startsWith('--color-') && v.startsWith('oklch')),
    ).toEqual([]);
  });

  it('defines slate-950 as the miStudio override, equal to tokens.bg.dark', () => {
    expect(theme['--color-slate-950']).toBe(tokens.bg.dark);
  });

  it('agrees with every brand token it names', () => {
    const pairs: [string, string][] = [
      [tokens.panel.dark, '--color-slate-900'],
      [tokens.card.dark, '--color-slate-800'],
      [tokens.line.dark, '--color-slate-700'],
      [tokens.line.light, '--color-slate-200'],
      [tokens.text.dark, '--color-slate-100'],
      [tokens.text.light, '--color-slate-900'],
      [tokens.dim.dark, '--color-slate-400'],
      [tokens.dim.light, '--color-slate-500'],
      [tokens.accent.dark, '--color-indigo-400'],
      [tokens.accent.light, '--color-indigo-600'],
      [tokens.accentFill.dark, '--color-indigo-500'],
      [tokens.green.dark, '--color-green-400'],
      [tokens.green.light, '--color-green-600'],
      [tokens.amber.dark, '--color-amber-400'],
      [tokens.amber.light, '--color-amber-600'],
      [tokens.red.dark, '--color-red-400'],
      [tokens.red.light, '--color-red-600'],
      [tokens.cyan.dark, '--color-cyan-400'],
      [tokens.cyan.light, '--color-cyan-600'],
      [tokens.emerald.dark, '--color-emerald-400'],
      [tokens.emerald.light, '--color-emerald-600'],
      [tokens.orange.dark, '--color-orange-400'],
      [tokens.orange.light, '--color-orange-600'],
    ];
    for (const [hex, name] of pairs) expect([name, theme[name]]).toEqual([name, hex]);
  });

  it('keeps the v3 absolute line heights for text sizes', () => {
    expect(theme['--text-sm--line-height']).toBe('1.25rem');
    expect(theme['--text-xs--line-height']).toBe('1rem');
    expect(rulesFor('.text-sm')).toContain('var(--text-sm--line-height)');
  });

  it('keeps the shell sizes and fonts', () => {
    expect(theme['--width-sidebar']).toBe('14rem');
    expect(theme['--width-sidebar-collapsed']).toBe('4rem');
    expect(theme['--height-topbar']).toBe('3.5rem');
    expect(rulesFor('.w-sidebar {')).toContain('var(--width-sidebar)');
    expect(rulesFor('.h-topbar')).toContain('var(--height-topbar)');
    expect(theme['--font-sans']).toMatch(/^Inter, system-ui/);
    expect(theme['--font-mono']).toMatch(/^'JetBrains Mono'/);
  });
});

describe('variants and Preflight carried from Tailwind CSS 3', () => {
  it('dark: follows the dark class with the v3 selector', () => {
    expect(css).toMatch(
      /\.dark\\:bg-slate-950:is\(\.dark \*\)\s*\{\s*background-color: var\(--color-slate-950\)/,
    );
    expect(css).not.toContain('prefers-color-scheme');
  });

  it('hover: applies on every device, as in v3', () => {
    expect(css).toContain('hover\\:bg-indigo-600');
    expect(css).not.toMatch(/@media \(hover: hover\)/);
  });

  it('restores the v3 default border colour, placeholder colour, cell padding and button cursor', () => {
    expect(css).toMatch(
      /::file-selector-button\s*\{\s*border-color: var\(--color-gray-200, currentcolor\)/,
    );
    expect(rulesFor('textarea::placeholder')).toContain('color: var(--color-gray-400)');
    expect(rulesFor('option {')).toContain('padding: revert');
    expect(css).toMatch(/\btd,\s*th\s*\{\s*padding: 1px;/);
    expect(rulesFor("[role='button']:not(:disabled)")).toContain('cursor: pointer');
  });

  it('stack-y-* gives every child after the first a top margin (v3 space-y-*)', () => {
    expect(css).toMatch(
      /\.stack-y-2 > :not\(\[hidden\]\) ~ :not\(\[hidden\]\)\s*\{\s*margin-top: calc\(var\(--spacing\) \* 2\);\s*margin-bottom: 0;/,
    );
  });
});

describe('source', () => {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = path.join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (
        /\.(tsx?|css)$/.test(name) &&
        !p.endsWith('tailwindTheme.test.ts') &&
        !p.endsWith('index.css')
      )
        files.push(p);
    }
  };
  walk(SRC);

  it('uses stack-y-* rather than the v4 space-y-*/space-x-* (different selector)', () => {
    expect(files.length).toBeGreaterThan(100);
    expect(files.some((f) => readFileSync(f, 'utf8').includes('stack-y-'))).toBe(true);
    const offenders = files.filter((f) =>
      /(^|[\s'"`:])-?space-[xy]-/m.test(readFileSync(f, 'utf8')),
    );
    expect(offenders.map((f) => path.relative(ROOT, f))).toEqual([]);
  });
});
