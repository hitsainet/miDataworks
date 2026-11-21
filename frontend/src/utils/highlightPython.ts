// A small Python tokenizer for showing a code rule's source (feature 018, FTID section 6; FTDD
// OI-6: the frontend carries no highlighter dependency). It only SPLITS the text into tokens; the
// caller renders each token as a React text node inside a <span>, so nothing in the source is ever
// read as HTML ("<script>" in a string shows as those characters). Concatenating the tokens' text
// gives back the source exactly, byte for byte (the test pins it), so the highlighted view never
// shows the operator something other than what was hashed.

export type PythonTokenKind = 'keyword' | 'builtin' | 'string' | 'comment' | 'number' | 'decorator' | 'plain';

export interface PythonToken {
  kind: PythonTokenKind;
  text: string;
}

const KEYWORDS = new Set([
  'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def',
  'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is',
  'lambda', 'match', 'case', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while', 'with',
  'yield',
]);

const BUILTINS = new Set([
  'abs', 'all', 'any', 'bool', 'dict', 'enumerate', 'filter', 'float', 'frozenset', 'int', 'isinstance',
  'len', 'list', 'map', 'max', 'min', 'ord', 'chr', 'range', 'reversed', 'set', 'sorted', 'str', 'sum',
  'tuple', 'zip', 'self',
]);

const IDENT = /[A-Za-z_][A-Za-z0-9_]*/y;
const NUMBER = /(?:0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?j?)/y;
const STRING_PREFIX = /(?:[rRbBuUfF]{1,2})?(?='|")/y;

/** The end index (exclusive) of the string literal whose quote starts at `at`. */
function stringEnd(source: string, at: number): number {
  const quote = source[at];
  const triple = source.startsWith(quote.repeat(3), at);
  const close = triple ? quote.repeat(3) : quote;
  let i = at + close.length;
  while (i < source.length) {
    if (source[i] === '\\') {
      i += 2;
      continue;
    }
    if (!triple && source[i] === '\n') return i; // an unterminated one-line string ends at the line
    if (source.startsWith(close, i)) return i + close.length;
    i += 1;
  }
  return source.length;
}

export function tokenizePython(source: string): PythonToken[] {
  const tokens: PythonToken[] = [];
  const push = (kind: PythonTokenKind, text: string) => {
    const last = tokens[tokens.length - 1];
    if (kind === 'plain' && last?.kind === 'plain') last.text += text;
    else tokens.push({ kind, text });
  };
  let i = 0;
  while (i < source.length) {
    const ch = source[i];
    if (ch === '#') {
      const end = source.indexOf('\n', i);
      const stop = end === -1 ? source.length : end;
      push('comment', source.slice(i, stop));
      i = stop;
      continue;
    }
    STRING_PREFIX.lastIndex = i;
    const prefix = STRING_PREFIX.exec(source);
    if (prefix && (ch === '"' || ch === "'" || /[rRbBuUfF]/.test(ch))) {
      const quoteAt = i + prefix[0].length;
      const end = stringEnd(source, quoteAt);
      push('string', source.slice(i, end));
      i = end;
      continue;
    }
    if (ch === '@' && (i === 0 || source[i - 1] === '\n' || /^[ \t]*$/.test(source.slice(source.lastIndexOf('\n', i - 1) + 1, i)))) {
      IDENT.lastIndex = i + 1;
      const name = IDENT.exec(source);
      if (name) {
        push('decorator', `@${name[0]}`);
        i += 1 + name[0].length;
        continue;
      }
    }
    if (/[A-Za-z_]/.test(ch)) {
      IDENT.lastIndex = i;
      const word = IDENT.exec(source)![0];
      push(KEYWORDS.has(word) ? 'keyword' : BUILTINS.has(word) ? 'builtin' : 'plain', word);
      i += word.length;
      continue;
    }
    if (/[0-9]/.test(ch) || (ch === '.' && /[0-9]/.test(source[i + 1] ?? ''))) {
      NUMBER.lastIndex = i;
      const num = NUMBER.exec(source);
      if (num && num[0].length > 0) {
        push('number', num[0]);
        i += num[0].length;
        continue;
      }
    }
    push('plain', ch);
    i += 1;
  }
  return tokens;
}

/** Tailwind classes per token kind, light and dark; the suite palette only (slate, indigo, status). */
export const TOKEN_CLASSES: Record<PythonTokenKind, string> = {
  keyword: 'text-indigo-700 dark:text-indigo-300 font-medium',
  builtin: 'text-cyan-700 dark:text-cyan-300',
  string: 'text-emerald-700 dark:text-emerald-300',
  comment: 'text-slate-500 dark:text-slate-400 italic',
  number: 'text-orange-700 dark:text-orange-300',
  decorator: 'text-amber-700 dark:text-amber-300',
  plain: '',
};
