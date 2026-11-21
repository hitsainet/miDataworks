import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { BEHAVIOUR_KEY_PATTERN, behaviourKeyOk } from './behaviourKey';

describe('behaviour key slug (015)', () => {
  it('is the backend pattern, character for character', () => {
    const backend = readFileSync(resolve(__dirname, '../../../backend/src/schemas/behaviour.py'), 'utf8');
    const match = backend.match(/BEHAVIOUR_KEY_PATTERN = r"([^"]+)"/);
    expect(match?.[1]).toBe(BEHAVIOUR_KEY_PATTERN.source);
  });
  it.each(['pii', 'high-stakes', 'ab', 'a'.repeat(64), ''])('accepts %j', (v) => expect(behaviourKeyOk(v)).toBe(true));
  it.each(['Pii', '-x', 'x-', 'a', 'a'.repeat(65), 'pii_us'])('refuses %j', (v) => expect(behaviourKeyOk(v)).toBe(false));
});
