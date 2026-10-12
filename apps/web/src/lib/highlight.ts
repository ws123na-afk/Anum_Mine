// Code highlighting for answers (docs/conversations.md#rendering): a small tokenizer that
// splits a code block into comments, strings, numbers, keywords and plain text. The
// renderer gives each token a class on a React text node, so code is never parsed as
// markup. A named language the table does not know still gets strings, numbers and comments.

export type CodeTokenKind = 'plain' | 'keyword' | 'string' | 'number' | 'comment' | 'literal';
export interface CodeToken { kind: CodeTokenKind; text: string }

const C_LIKE = 'break case catch class const continue default do else enum export extends finally for function if import in instanceof interface let new of package private protected public return static super switch this throw try type typeof var void while yield async await implements namespace readonly declare as from struct func go defer chan map range select fn impl mut pub use mod trait where match loop';
const KEYWORDS: Record<string, string> = {
  js: C_LIKE, ts: C_LIKE, javascript: C_LIKE, typescript: C_LIKE, jsx: C_LIKE, tsx: C_LIKE, java: C_LIKE, kotlin: `${C_LIKE} fun val when object data sealed`,
  c: C_LIKE, cpp: C_LIKE, 'c++': C_LIKE, cs: C_LIKE, csharp: C_LIKE, go: C_LIKE, rust: C_LIKE, rs: C_LIKE, swift: `${C_LIKE} guard func let var`, dart: `${C_LIKE} final late required factory dynamic`,
  python: 'and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case',
  py: 'and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case',
  bash: 'if then else elif fi for while until do done case esac function in return export local readonly set unset echo exit',
  sh: 'if then else elif fi for while until do done case esac function in return export local readonly set unset echo exit',
  shell: 'if then else elif fi for while until do done case esac function in return export local readonly set unset echo exit',
  sql: 'select from where and or not insert into values update set delete create table index view drop alter add column primary key foreign references join left right inner outer on group by order having limit offset as distinct union all case when then else end begin commit rollback grant revoke with returning null is in like between exists',
  yaml: '', yml: '', json: '', css: 'important media supports import', html: '', xml: '',
};
const LITERALS = new Set(['true', 'false', 'null', 'undefined', 'none', 'nil', 'True', 'False', 'None', 'NULL', 'TRUE', 'FALSE']);
const HASH_COMMENTS = new Set(['python', 'py', 'bash', 'sh', 'shell', 'yaml', 'yml', 'ruby', 'rb', 'r', 'toml', 'dockerfile', 'makefile', 'perl', 'powershell', 'ps1']);
const DASH_COMMENTS = new Set(['sql', 'lua', 'haskell', 'hs']);

/** Split `code` into highlight tokens; joining their text gives `code` back exactly. */
export function highlight(code: string, lang: string): CodeToken[] {
  const language = lang.toLowerCase();
  // No language named (often prose or output): shown as is, an apostrophe is not a string.
  if (!language || language === 'text' || language === 'txt' || language === 'plaintext') return code ? [{ kind: 'plain', text: code }] : [];
  const keywords = new Set((KEYWORDS[language] ?? C_LIKE).split(' ').filter(Boolean));
  const caseless = language === 'sql';
  const hash = HASH_COMMENTS.has(language);
  const dash = DASH_COMMENTS.has(language);
  const slash = !hash && !dash && language !== 'html' && language !== 'xml';
  const tokens: CodeToken[] = [];
  const push = (kind: CodeTokenKind, text: string) => {
    const prev = tokens[tokens.length - 1];
    if (prev && prev.kind === kind) prev.text += text;
    else if (text) tokens.push({ kind, text });
  };
  let i = 0;
  while (i < code.length) {
    const rest = code.slice(i);
    const ch = code[i];
    let m: RegExpExecArray | null;
    if ((slash && rest.startsWith('//')) || (hash && ch === '#') || (dash && rest.startsWith('--'))) {
      const end = code.indexOf('\n', i);
      const stop = end < 0 ? code.length : end;
      push('comment', code.slice(i, stop));
      i = stop;
    } else if (slash && rest.startsWith('/*')) {
      const end = code.indexOf('*/', i + 2);
      const stop = end < 0 ? code.length : end + 2;
      push('comment', code.slice(i, stop));
      i = stop;
    } else if ((language === 'html' || language === 'xml') && rest.startsWith('<!--')) {
      const end = code.indexOf('-->', i + 4);
      const stop = end < 0 ? code.length : end + 3;
      push('comment', code.slice(i, stop));
      i = stop;
    } else if (ch === '"' || ch === "'" || ch === '`') {
      let j = i + 1;
      while (j < code.length && code[j] !== ch && !(code[j] === '\n' && ch !== '`')) j += code[j] === '\\' ? 2 : 1;
      const stop = Math.min(code.length, j + 1);
      push('string', code.slice(i, stop));
      i = stop;
    } else if ((m = /^(?:0x[\da-f]+|\d[\d_]*(?:\.\d+)?(?:e[+-]?\d+)?)/i.exec(rest)) && !/[\w$]/.test(code[i - 1] ?? '')) {
      push('number', m[0]);
      i += m[0].length;
    } else if ((m = /^[A-Za-z_$][\w$]*/.exec(rest))) {
      const word = m[0];
      if (LITERALS.has(word)) push('literal', word);
      else push(keywords.has(caseless ? word.toLowerCase() : word) ? 'keyword' : 'plain', word);
      i += word.length;
    } else {
      push('plain', ch);
      i += 1;
    }
  }
  return tokens;
}

/** A language name to show above a code block, or '' when the fence named none. */
export function languageLabel(lang: string): string {
  const names: Record<string, string> = {
    js: 'JavaScript', javascript: 'JavaScript', ts: 'TypeScript', typescript: 'TypeScript', tsx: 'TSX', jsx: 'JSX', py: 'Python', python: 'Python',
    sh: 'Shell', bash: 'Bash', shell: 'Shell', sql: 'SQL', json: 'JSON', yaml: 'YAML', yml: 'YAML', html: 'HTML', css: 'CSS', go: 'Go', rust: 'Rust',
    rs: 'Rust', java: 'Java', kotlin: 'Kotlin', dart: 'Dart', swift: 'Swift', c: 'C', cpp: 'C++', cs: 'C#', csharp: 'C#', xml: 'XML', md: 'Markdown', markdown: 'Markdown',
  };
  return names[lang.toLowerCase()] ?? (lang ? lang : '');
}
