// 018 FTASKS 16.1: the code-rule source highlighter. Spans only, the source back byte for byte,
// and nothing in the source is ever read as HTML.
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { PythonSource } from '@/components/approvals/CodeRuleCard';

import { tokenizePython } from './highlightPython';

const SOURCE = [
  '@cache',
  'def count_cards(text: str, params) -> int:',
  '    # count candidates',
  "    n = 0x1F + 3.5e2 + len(text)",
  '    s = r"\\d{4}" + \'it\\\'s\' + """multi',
  'line"""',
  '    return n if True else None',
].join('\n');

const kinds = (src: string) => tokenizePython(src).filter((t) => t.kind !== 'plain').map((t) => [t.kind, t.text]);

describe('tokenizePython', () => {
  it('gives back the source exactly', () => {
    expect(tokenizePython(SOURCE).map((t) => t.text).join('')).toBe(SOURCE);
  });

  it('marks keywords, builtins, strings, comments, numbers and decorators', () => {
    const found = kinds(SOURCE);
    expect(found).toContainEqual(['decorator', '@cache']);
    expect(found).toContainEqual(['keyword', 'def']);
    expect(found).toContainEqual(['keyword', 'return']);
    expect(found).toContainEqual(['keyword', 'True']);
    expect(found).toContainEqual(['keyword', 'None']);
    expect(found).toContainEqual(['builtin', 'len']);
    expect(found).toContainEqual(['comment', '# count candidates']);
    expect(found).toContainEqual(['number', '0x1F']);
    expect(found).toContainEqual(['number', '3.5e2']);
    expect(found).toContainEqual(['string', 'r"\\d{4}"']);
    expect(found).toContainEqual(['string', "'it\\'s'"]);
    expect(found).toContainEqual(['string', '"""multi\nline"""']);
  });

  it('does not take a keyword inside a name or a "#" inside a string', () => {
    expect(kinds('define = "a # b"')).toEqual([['string', '"a # b"']]);
  });

  it('ends an unterminated one-line string at the line and keeps going', () => {
    const tokens = tokenizePython('x = "open\nreturn 1');
    expect(tokens.map((t) => t.text).join('')).toBe('x = "open\nreturn 1');
    expect(tokens).toContainEqual({ kind: 'keyword', text: 'return' });
  });
});

describe('PythonSource renders text, never HTML', () => {
  it('shows "<script>" in the source as characters', () => {
    const hostile = 's = "<script>alert(1)</script>"  # <img src=x onerror=alert(2)>\n<b>bold</b>';
    const { container } = render(<PythonSource source={hostile} />);
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(container.querySelector('pre')?.textContent).toBe(hostile);
    // only the highlighter's own spans inside the code element
    const tags = new Set(Array.from(container.querySelectorAll('code *')).map((el) => el.tagName));
    expect([...tags]).toEqual(['SPAN']);
  });
});
