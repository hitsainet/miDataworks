import { describe, expect, it } from 'vitest';

import { urlOrigin } from './urlOrigin';

// The same answers as backend tests/unit/test_key_binding.py ORIGINS (the cases both can parse).
const CASES: [string | null, string | null][] = [
  ['http://millm.test/v1', 'http://millm.test:80'],
  ['http://millm.test:80/v1', 'http://millm.test:80'],
  ['HTTP://MiLLM.Test/v1/', 'http://millm.test:80'],
  ['https://api.example.com/v1/systemone', 'https://api.example.com:443'],
  ['https://api.example.com:8443', 'https://api.example.com:8443'],
  ['http://127.0.0.1:8000/v1', 'http://127.0.0.1:8000'],
  ['http://[::1]:9000/v1', 'http://[::1]:9000'],
  ['http://user:pw@host.test/v1', 'http://host.test:80'],
  ['  http://millm.test/v1  ', 'http://millm.test:80'],
  ['millm.test/v1', null],
  ['ftp://files.test/x', null],
  ['', null],
  [null, null],
];

describe('urlOrigin', () => {
  it.each(CASES)('should give %s the origin %s', (url, origin) => {
    expect(urlOrigin(url)).toBe(origin);
  });
});
