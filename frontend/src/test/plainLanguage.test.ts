// The plain-language guard over the in-app Help text (ENH-004 decision D8, feature 013). Help is
// read by people new to the field, so it must not carry planning ids, and every acronym is spelled
// out at its first use in a topic: "supervised fine-tuning (SFT)" or "AUROC (area under ...)".
// The negative control at the bottom proves the guard goes red on exactly what it exists to catch.
import { describe, expect, it } from 'vitest';

import { HELP_TOPICS, type HelpTopic } from '@/config/help';

/** Internal ids and planning language (the manual's publish rules use the same list). */
const INTERNAL: Array<[string, RegExp]> = [
  ['requirement id', /\bFR-\d/],
  ['business requirements id', /\bBRD-/],
  ['architecture decision id', /\bADR-\d/],
  ['end-to-end finding id', /\bMIS-E2E/],
  ['decision id', /\(D\d+\)/],
  ['product decision id', /\b[PTX]-\d{2}\b/],
  ['planning folder', /0xcc\//],
  ['task list', /\bFTASKS\b|\bFTID\b/],
];

/** Abbreviations that are ordinary words to a reader and need no expansion. */
export const PLAIN_ABBREVIATIONS = new Set(['ID', 'URL', 'API', 'JSON', 'JSONL', 'CSV', 'HTTP', 'GB']);

function text(topic: HelpTopic): string {
  return [topic.title, ...topic.body, ...(topic.steps ?? [])].join('\n');
}

/** Every problem in one topic, in words. Empty means the topic passes. */
export function plainLanguageProblems(topic: HelpTopic): string[] {
  const all = text(topic);
  const problems: string[] = [];
  for (const [name, pattern] of INTERNAL) {
    const hit = all.match(pattern);
    if (hit) problems.push(`${topic.id}: ${name} "${hit[0]}"`);
  }
  const seen = new Set<string>();
  for (const match of all.matchAll(/\b[A-Z][A-Z0-9]{1,}\b/g)) {
    const word = match[0];
    if (PLAIN_ABBREVIATIONS.has(word) || seen.has(word)) continue;
    seen.add(word);
    const at = match.index ?? 0;
    const inParentheses = all[at - 1] === '(';
    const expandedAfter = all.slice(at + word.length).startsWith(' (');
    if (!inParentheses && !expandedAfter) problems.push(`${topic.id}: "${word}" is not spelled out at its first use`);
  }
  return problems;
}

describe('Help text is plain language (ENH-004 D8)', () => {
  it('no topic carries an internal id or a bare acronym', () => {
    expect(HELP_TOPICS.flatMap(plainLanguageProblems)).toEqual([]);
  });

  it('the acronyms the review named are spelled out', () => {
    const everything = HELP_TOPICS.map(text).join('\n');
    expect(everything).toContain('supervised fine-tuning (SFT)');
    expect(everything).toContain('direct preference optimization (DPO)');
    expect(everything).toContain('Kahneman-Tversky optimization (KTO)');
    expect(everything).toMatch(/receiver operating characteristic curve \(AUROC/);
  });

  it('negative control: a planning id and a bare AUROC go red', () => {
    const bad: HelpTopic = { id: 'synthetic', title: 'Bad', body: ['Implements FR-009.1 and reports AUROC.'], manual: 'intro' };
    const problems = plainLanguageProblems(bad);
    expect(problems).toContain('synthetic: requirement id "FR-0"');
    expect(problems).toContain('synthetic: "AUROC" is not spelled out at its first use');
  });

  it('negative control: a spelled-out acronym passes, in either order', () => {
    const good: HelpTopic = { id: 'ok', title: 'Fine', body: ['Uses supervised fine-tuning (SFT). AUROC (area under the curve) is shown.'], manual: 'intro' };
    expect(plainLanguageProblems(good)).toEqual([]);
  });
});
