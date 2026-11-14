import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { validateHfRepoId } from './hfRepoId';

// The same case file the backend's test_hf_repo_id_rule.py reads: both sides give one verdict.
const cases = JSON.parse(readFileSync(resolve(__dirname, '../../../docs/schemas/hf-repo-id-cases.json'), 'utf8')) as {
  valid: string[];
  invalid: string[];
  too_long: string;
};

describe('validateHfRepoId (shared case file)', () => {
  it.each(cases.valid)('accepts %s', (id) => expect(validateHfRepoId(id)).toBe(true));
  it.each([...cases.invalid, cases.too_long])('refuses %j', (id) => expect(validateHfRepoId(id)).not.toBe(true));
  it('says what a repository ID looks like', () => {
    expect(validateHfRepoId('colbert')).toMatch(/owner\/name/);
  });
});
