#!/usr/bin/env node
// Copies the allow-listed user pages from ../docs/ into the manual (docs/guide/, docs/reference/).
//
// docs/*.md stays the single source: the application, its code comments and the tests point at those
// files. This script is the ONLY way a docs/ page reaches the public site, and it reads exactly the
// pages named in publish-allowlist.json — the same list the mirror sync keeps (DOCS_KEPT), pinned
// together by backend/tests/unit/test_manual_publish.py.
//
// It refuses (exit 1), so `npm run build` fails and CI goes red, when:
//   - an allow-listed page is missing;
//   - a page contains a forbidden pattern (internal hostname, private address, private planning
//     path, token, ...): the list is in publish-allowlist.json;
//   - a table row has more cells than its header — the usual cause is a `|` inside inline code,
//     which GFM splits into a new cell unless it is written `\|`;
//   - a relative link points at a docs/ page that is not published (it would be a broken link, and
//     would name a private page).
//
// Run by `npm run build` and `npm start` (the prebuild/prestart hooks). The output directories are
// generated and git-ignored; never edit them.
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const MANUAL = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DOCS = resolve(MANUAL, '..', 'docs');
const POLICY = JSON.parse(readFileSync(join(MANUAL, 'publish-allowlist.json'), 'utf8'));
const REPO_URL = 'https://github.com/hitsainet/miDataworks';
const SECTIONS = ['guide', 'reference'];

const errors = [];
const fail = (msg) => errors.push(msg);

const bySource = new Map(POLICY.pages.map((p) => [p.source, p]));
const forbidden = POLICY.forbidden.map((f) => ({ name: f.name, re: new RegExp(f.pattern) }));

/** Lines inside fenced code blocks, so table and link checks skip them (the scan does not). */
function fencedMask(lines) {
  const mask = [];
  let fence = null;
  for (const line of lines) {
    const m = line.match(/^\s*(```+|~~~+)/);
    if (fence) {
      mask.push(true);
      if (m && m[1][0] === fence[0] && m[1].length >= fence.length) fence = null;
    } else if (m) {
      fence = m[1];
      mask.push(true);
    } else mask.push(false);
  }
  return mask;
}

/** Cells in a GFM table row: split on unescaped pipes, ignoring the outer ones. */
function cellCount(row) {
  const body = row.trim().replace(/^\|/, '').replace(/\|$/, '');
  return body.split(/(?<!\\)\|/).length;
}

const isDelimiterRow = (line) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line);

function checkTables(file, lines, mask) {
  for (let i = 1; i < lines.length; i++) {
    if (mask[i] || !isDelimiterRow(lines[i]) || !lines[i - 1].includes('|')) continue;
    const width = cellCount(lines[i - 1]);
    for (let j = i + 1; j < lines.length && lines[j].trim().startsWith('|') && !mask[j]; j++) {
      const n = cellCount(lines[j]);
      if (n > width) {
        fail(
          `${file}:${j + 1}: table row has ${n} cells but its header has ${width} ` +
            '(a "|" inside inline code must be written "\\|")',
        );
      }
    }
  }
}

function rewriteLinks(file, line, lineNo, page) {
  return line.replace(/\]\(([^)\s]+)\)/g, (whole, target) => {
    if (/^[a-z][a-z0-9+.-]*:/i.test(target) || target.startsWith('#')) return whole;
    const [path, anchor] = target.split('#');
    const clean = path.replace(/^\.\//, '');
    if (clean.startsWith('schemas/')) return `](${REPO_URL}/blob/main/docs/${clean}${anchor ? `#${anchor}` : ''})`;
    const linked = bySource.get(clean);
    if (!linked) {
      fail(`${file}:${lineNo}: links to "${target}", which is not a published page (publish-allowlist.json)`);
      return whole;
    }
    const rel = linked.section === page.section ? `${linked.id}.md` : `../${linked.section}/${linked.id}.md`;
    return `](${rel}${anchor ? `#${anchor}` : ''})`;
  });
}

const yamlString = (s) => JSON.stringify(s);

for (const section of SECTIONS) rmSync(join(MANUAL, 'docs', section), { recursive: true, force: true });

const seen = new Set();
for (const page of POLICY.pages) {
  if (!SECTIONS.includes(page.section)) fail(`${page.source}: unknown section "${page.section}"`);
  if (seen.has(`${page.section}/${page.id}`)) fail(`${page.source}: duplicate id ${page.section}/${page.id}`);
  seen.add(`${page.section}/${page.id}`);
  const file = `docs/${page.source}`;
  const src = join(DOCS, page.source);
  if (!existsSync(src)) {
    fail(`${file}: allow-listed in publish-allowlist.json but missing`);
    continue;
  }
  const lines = readFileSync(src, 'utf8').split('\n');
  lines.forEach((line, i) => {
    for (const f of forbidden) if (f.re.test(line)) fail(`${file}:${i + 1}: ${f.name} must not be published`);
  });
  const mask = fencedMask(lines);
  checkTables(file, lines, mask);

  // The first H1 becomes the page title, without the internal "(feature NNN)" suffix.
  const h1 = lines.findIndex((l, i) => !mask[i] && /^#\s+/.test(l));
  const title = h1 >= 0 ? lines[h1].replace(/^#\s+/, '').replace(/\s*\((feature \d+|[A-Z]-\d+[^)]*)\)\s*$/, '').trim() : page.label;
  const body = lines
    .filter((_, i) => i !== h1)
    .map((l, i) => (mask[i] ? l : rewriteLinks(file, l, i + 1, page)));

  const front = [
    '---',
    `id: ${page.id}`,
    `title: ${yamlString(title)}`,
    `sidebar_label: ${yamlString(page.label)}`,
    `custom_edit_url: ${yamlString(`${REPO_URL}/blob/main/${file}`)}`,
    '---',
    '',
    `<!-- GENERATED from ${file} by manual/scripts/sync-docs.mjs. Edit the source, not this file. -->`,
    '',
  ];
  const out = join(MANUAL, 'docs', page.section, `${page.id}.md`);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, [...front, ...body].join('\n'));
}

// The hand-written manual pages are published too, so they get the same scan.
const handWritten = readdirSync(join(MANUAL, 'docs')).filter((n) => /\.mdx?$/.test(n));
if (!handWritten.includes('intro.md')) fail('manual/docs/intro.md: missing (the site root)');
for (const name of handWritten) {
  const p = join(MANUAL, 'docs', name);
  readFileSync(p, 'utf8')
    .split('\n')
    .forEach((line, i) => {
      for (const f of forbidden) if (f.re.test(line)) fail(`manual/docs/${name}:${i + 1}: ${f.name} must not be published`);
    });
}

if (errors.length) {
  console.error(`sync-docs: ${errors.length} problem(s); the manual is not built:`);
  for (const e of errors) console.error(`  - ${e}`);
  process.exit(1);
}
console.log(`sync-docs: ${POLICY.pages.length} pages copied from docs/`);
