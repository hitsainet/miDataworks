import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HelpLink } from '@/components/help/HelpLink';
import { HelpPanel } from '@/components/help/HelpPanel';
import { HELP_TOPICS } from '@/config/help';
import { DEFAULT_MANUAL_BASE_URL, MANUAL_BASE_URL, manualBaseUrl, manualUrl } from '@/config/manual';
import { getPanel } from '@/config/panels';

// The Help screen and every per-step Help link point into the public manual (manual/, published
// to GitHub Pages from the mirror). These checks read the manual's own sources, so a renamed page,
// a renamed heading or a page dropped from the publish allow-list fails here, not in a user's
// browser as a 404.
const REPO = resolve(__dirname, '../../..');
const read = (p: string) => readFileSync(resolve(REPO, p), 'utf8');

interface AllowedPage { source: string; section: string; id: string }
const ALLOWLIST: { pages: AllowedPage[] } = JSON.parse(read('manual/publish-allowlist.json'));

/** The Markdown file a manual page path is built from (generated pages come from docs/). */
function sourceOf(page: string): string {
  const path = page.split('#')[0];
  const parts = path.split('/');
  if (parts.length === 2) {
    const entry = ALLOWLIST.pages.find((p) => p.section === parts[0] && p.id === parts[1]);
    if (!entry) throw new Error(`${path} is not a published page (manual/publish-allowlist.json)`);
    return `docs/${entry.source}`;
  }
  return `manual/docs/${path}.md`;
}

/** Heading ids as Docusaurus makes them (github-slugger): lower case, punctuation dropped, spaces to "-". */
function slug(heading: string): string {
  return heading
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\p{Pc}\- ]/gu, '')
    .replace(/ /g, '-');
}

function headingSlugs(markdown: string): string[] {
  let fenced = false;
  const out: string[] = [];
  for (const line of markdown.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    const m = !fenced && line.match(/^#{2,6}\s+(.*)$/);
    if (m) out.push(slug(m[1]));
  }
  return out;
}

describe('the manual address comes from config', () => {
  it('the default agrees with the published site (manual/docusaurus.config.ts url + baseUrl)', () => {
    const config = read('manual/docusaurus.config.ts');
    const url = config.match(/^\s*url:\s*'([^']+)'/m)?.[1];
    const baseUrl = config.match(/^\s*baseUrl:\s*'([^']+)'/m)?.[1];
    expect(url && baseUrl).toBeTruthy();
    expect(DEFAULT_MANUAL_BASE_URL).toBe(`${url}${baseUrl}`);
    expect(MANUAL_BASE_URL).toBe(DEFAULT_MANUAL_BASE_URL);
  });

  it('a configured base replaces the default host, with or without a trailing slash', () => {
    expect(manualBaseUrl('https://docs.example.test/dw')).toBe('https://docs.example.test/dw/');
    expect(manualBaseUrl('  ')).toBe(DEFAULT_MANUAL_BASE_URL);
    expect(manualUrl('guide/labeling#protocols', 'https://docs.example.test/dw/')).toBe(
      'https://docs.example.test/dw/guide/labeling#protocols',
    );
  });
});

describe('every help topic names a manual page and section that exist', () => {
  it.each(HELP_TOPICS.map((t) => [t.id, t.manual] as const))('%s -> %s', (_id, page) => {
    expect(page).toBeTruthy();
    const source = sourceOf(page);
    expect(existsSync(resolve(REPO, source)), `${source} is missing`).toBe(true);
    const anchor = page.split('#')[1];
    if (anchor) expect(headingSlugs(read(source))).toContain(anchor);
  });

  it('the slug rule matches Docusaurus on headings with punctuation', () => {
    expect(slug('Leakage, contamination, profile and clusters')).toBe('leakage-contamination-profile-and-clusters');
    expect(slug('1. Import')).toBe('1-import');
    expect(slug('`core` (7)')).toBe('core-7');
  });
});

describe('the Help screen links each summary to its manual page', () => {
  it('one "Read more in the manual" link per topic, to that topic\'s page, in a new tab', () => {
    render(<HelpPanel panel={getPanel('help')} />);
    for (const t of HELP_TOPICS) {
      const link = screen.getByTestId(`help-manual-${t.id}`);
      expect(link).toHaveAttribute('href', `${MANUAL_BASE_URL}${t.manual}`);
      expect(link).toHaveAttribute('target', '_blank');
      expect(link.getAttribute('rel')).toContain('noopener');
      expect(link).toHaveTextContent('Read more in the manual');
    }
    expect(screen.getAllByRole('link', { name: /Read more in the manual/ })).toHaveLength(HELP_TOPICS.length);
    expect(screen.getByTestId('manual-home')).toHaveAttribute('href', MANUAL_BASE_URL);
  });

  it('keeps the in-app text as the summary', () => {
    render(<HelpPanel panel={getPanel('help')} />);
    expect(document.getElementById('help-curate')).toHaveTextContent('Sample rows');
  });
});

describe('a per-step Help link carries its manual page', () => {
  it.each(HELP_TOPICS.map((t) => [t.id, t.manual] as const))('%s', (id, page) => {
    render(<HelpLink topic={id} label="Help with this step" />);
    expect(screen.getByRole('button', { name: 'Help with this step' })).toBeInTheDocument();
    const link = screen.getByTestId(`manual-link-${id}`);
    expect(link).toHaveAttribute('href', `${MANUAL_BASE_URL}${page}`);
    expect(link).toHaveAttribute('target', '_blank');
    expect(screen.getAllByRole('link')).toHaveLength(1);
  });

  it('an unknown topic shows the Help button and no manual link', () => {
    render(<HelpLink topic="no-such-topic" />);
    expect(screen.queryByRole('link')).toBeNull();
  });
});
