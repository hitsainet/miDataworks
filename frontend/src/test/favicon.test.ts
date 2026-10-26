import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// The browser tab showed a generic globe because index.html linked no icon. The mark is the
// sidebar's logo tile (Sidebar.tsx: indigo-500 rounded-lg, white lucide Factory glyph). The
// Playwright shell walk checks the BUILT app serves the file (e2e/shell.spec.ts).
const root = resolve(__dirname, '../..');
const read = (p: string) => readFileSync(resolve(root, p), 'utf8');

describe('favicon', () => {
  it('index.html links the SVG favicon', () => {
    expect(read('index.html')).toMatch(
      /<link rel="icon" type="image\/svg\+xml" href="\/favicon\.svg"\s*\/>/,
    );
  });

  it('ships in public/ (Vite copies it to the build root) as a well-formed SVG', () => {
    const svgPath = resolve(root, 'public/favicon.svg');
    expect(existsSync(svgPath)).toBe(true);
    const doc = new DOMParser().parseFromString(read('public/favicon.svg'), 'image/svg+xml');
    expect(doc.getElementsByTagName('parsererror')).toHaveLength(0);
    expect(doc.documentElement.tagName).toBe('svg');
    expect(doc.documentElement.getAttribute('viewBox')).toBe('0 0 32 32');
  });

  it('draws the sidebar mark: the brand fill and the same lucide glyph the sidebar imports', () => {
    const svg = read('public/favicon.svg');
    const sidebar = read('src/components/layout/Sidebar.tsx');
    const brand = read('src/config/brand.ts');
    const fill = /accentFill: \{ dark: '(#[0-9a-f]{6})'/.exec(brand)?.[1];
    expect(fill).toBeDefined();
    expect(svg).toContain(`fill="${fill}"`);
    expect(sidebar).toMatch(/data-testid="logo-tile"[\s\S]{0,120}<Factory /);
    // lucide's factory body path; if the sidebar's icon changes, this test says so. (lucide-react
    // 1.x ships its ESM icons as .mjs; 0.x shipped .js.)
    const glyph = read('node_modules/lucide-react/dist/esm/icons/factory.mjs');
    const body = /d: "(M3 19a2[^"]+)"/.exec(glyph)?.[1];
    expect(body).toBeDefined();
    expect(svg).toContain(`d="${body}"`);
  });

  it('nginx serves the file before the SPA fallback', () => {
    const conf = read('nginx.conf');
    expect(conf).toMatch(/location \/ \{\s*try_files \$uri \$uri\/ \/index\.html;/);
    expect(conf).toMatch(/gzip_types[^;]*image\/svg\+xml/);
  });
});
