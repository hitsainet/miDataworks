import { expect, test } from '@playwright/test';

import { stubApi } from './fixtures';
import { api005 } from './fixtures005';
import { api007 } from './fixtures007';

// Template and rubric authoring (UI parity with the API and MCP): Generation → Templates and
// Label runs → Rubrics, light and dark, against route fixtures.
const theme = (t: string) =>
  window.localStorage.setItem('midataworks-ui-preferences', JSON.stringify({ state: { theme: t, sidebar: { collapsed: false, mobileOpen: false } }, version: 0 }));

for (const mode of ['dark', 'light'] as const) {
  test(`generation templates: new, refused, saved, cloned (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { extra: api007(seen) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/generation');
    await page.getByRole('button', { name: 'Templates' }).click();
    const library = page.getByTestId('template-library');
    await expect(library).toContainText('minimal-pair-v1@1');

    await library.getByRole('button', { name: 'New template' }).click();
    const form = page.getByTestId('template-form');
    await form.getByLabel('Template name').fill('respond-v1');
    await form.getByLabel('Prompt').fill('{prompt}');
    await form.getByRole('button', { name: 'Save template' }).click();
    await expect(form.getByTestId('template-refusal')).toContainText("A template named 'respond-v1' exists; clone it to make version 2.");

    await form.getByLabel('Template name').fill('topic-respond');
    await form.getByLabel('Template description').fill('Answers a prompt about one topic.');
    await form.getByLabel('System message').fill('Answer in two sentences.');
    await form.getByLabel('Prompt').fill('Topic: {topic}\n{prompt}');
    await form.getByLabel('Structured output').selectOption('json_schema');
    await form.getByLabel('JSON schema').fill('{"type": object}');
    await form.getByRole('button', { name: 'Save template' }).click();
    await expect(form.getByTestId('template-refusal')).toContainText('The JSON schema is not valid JSON');
    await page.screenshot({ path: `e2e/captures/${mode}-generation-template-new.png`, fullPage: true });

    await form.getByLabel('JSON schema').fill('{"type": "object", "properties": {"answer": {"type": "string"}}}');
    await form.getByRole('button', { name: 'Save template' }).click();
    const notice = page.getByTestId('saved-template');
    await expect(notice).toContainText('Saved as topic-respond@1.');
    await expect(notice.getByTestId('saved-placeholders')).toContainText('{topic}');
    await page.screenshot({ path: `e2e/captures/${mode}-generation-template-saved.png`, fullPage: true });
    expect(seen.filter((c) => c.method === 'POST' && c.path === '/api/v1/generation-templates')).toHaveLength(2);

    await library.getByRole('button', { name: 'Clone minimal-pair-v1@1' }).click();
    const clone = page.getByTestId('template-form');
    await expect(clone.getByLabel('Prompt')).toHaveValue(/\{prompt\}/);
    await clone.getByLabel('Prompt').fill('Flip the concept "formality" with the smallest edit.\n\nText:\n{prompt}');
    await page.screenshot({ path: `e2e/captures/${mode}-generation-template-clone.png`, fullPage: true });
    await clone.getByRole('button', { name: 'Save the clone' }).click();
    await expect(page.getByTestId('saved-template')).toContainText('Saved as minimal-pair-v1@2.');
    const sent = seen.filter((c) => c.method === 'POST' && c.path === '/api/v1/generation-templates/gt_m/clone');
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toMatchObject({ body: { prompt: 'Flip the concept "formality" with the smallest edit.\n\nText:\n{prompt}', sampling: { temperature: 0.2 } } });
  });

  test(`rubrics: new pairwise rubric and export (${mode})`, async ({ page }) => {
    const seen: Array<{ method: string; path: string; body: unknown }> = [];
    await stubApi(page, { extra: api005(seen) });
    await page.addInitScript(theme, mode);
    await page.goto('/#/label-runs');
    await page.getByRole('button', { name: 'Rubrics' }).click();
    const library = page.getByTestId('rubric-library');
    await expect(library).toContainText('humor/judge@1');

    await library.getByRole('button', { name: 'New rubric' }).click();
    const form = page.getByTestId('rubric-form');
    await form.getByLabel('Rubric name').fill('helpfulness/pairwise');
    await form.getByLabel('Rubric style').selectOption('pairwise');
    await form.getByLabel('Message 1 content').fill('You compare two answers to the same prompt.');
    await form.getByLabel('Message 2 content').fill('Prompt: {prompt}\n\nAnswer A: {a}\n\nAnswer B: {b}\n\nWhich is more helpful? Reply A, B or tie.');
    await form.getByLabel('Input fields').fill('prompt');
    await form.getByLabel('Allowed verdicts').fill('A, B, tie');
    await form.getByLabel('Pair field A').fill('chosen');
    await form.getByLabel('Pair field B').fill('rejected');
    await form.getByLabel('Swap map').fill('A=B, B=A, tie=tie');
    await page.screenshot({ path: `e2e/captures/${mode}-rubric-new.png`, fullPage: true });
    await form.getByRole('button', { name: 'Save rubric' }).click();
    await expect(page.getByTestId('rubric-notice')).toContainText('Saved helpfulness/pairwise@1 (pairwise).');
    const sent = seen.filter((c) => c.method === 'POST' && c.path === '/api/v1/rubrics');
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toMatchObject({ name: 'helpfulness/pairwise', body: { style: 'pairwise', pair_fields: ['chosen', 'rejected'], swap_map: { A: 'B', B: 'A', tie: 'tie' } } });

    const download = page.waitForEvent('download');
    await library.getByRole('button', { name: 'Export humor/judge@1' }).click();
    expect((await download).suggestedFilename()).toBe('humor_judge@1.rubric.json');
    await page.screenshot({ path: `e2e/captures/${mode}-rubric-library.png`, fullPage: true });
  });
}
