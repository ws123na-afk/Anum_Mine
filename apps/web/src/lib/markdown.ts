// A safe Markdown reading of assistant answers (docs/conversations.md#rendering).
// Output is data (blocks and inline spans) that the renderer turns into React text
// nodes, so model output can never inject markup. Links survive only for http(s) and
// mailto targets; anything else is shown as its text. No browser APIs: unit tested
// directly by `pnpm test:unit`.

export type MdInline =
  | { kind: 'text' | 'strong' | 'em' | 'code' | 'del'; text: string }
  | { kind: 'link'; text: string; href: string };

export type MdAlign = 'left' | 'center' | 'right' | null;

export type MdBlock =
  | { kind: 'heading'; level: 1 | 2 | 3; inline: MdInline[] }
  | { kind: 'paragraph'; inline: MdInline[] }
  | { kind: 'list'; ordered: boolean; start: number; items: MdInline[][] }
  | { kind: 'code'; lang: string; text: string }
  | { kind: 'table'; header: MdInline[][]; align: MdAlign[]; rows: MdInline[][][] }
  | { kind: 'quote'; inline: MdInline[] }
  | { kind: 'rule' };

/** A link target the page may open: http(s) or mailto only, without control characters or spaces. */
export function safeHref(raw: string): string | null {
  const href = raw.trim();
  if (!href || /[\s\u0000-\u001f\u007f<>"]/.test(href)) return null;
  if (/^mailto:[^@\s]+@[^@\s]+$/i.test(href)) return href;
  try {
    const url = new URL(href);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

const INLINE = new RegExp(
  [
    '(`[^`\\n]+`)', // 1 code
    '(\\[([^\\]\\n]+)\\]\\(([^)\\s]+)\\))', // 2 link: 3 text, 4 target
    '(\\*\\*[^*\\n]+\\*\\*|__[^_\\n]+__)', // 5 strong
    '(~~[^~\\n]+~~)', // 6 strikethrough
    '(\\*[^*\\s][^*\\n]*\\*|\\b_[^_\\n]+_\\b)', // 7 em
    '(https?:\\/\\/[^\\s<>()]+[^\\s<>().,;:!?\'"])', // 8 bare URL
  ].join('|'),
  'g',
);

/** Inline spans: code, links, bold, italics, strikethrough and bare URLs; the rest is text. */
export function parseMdInline(text: string): MdInline[] {
  const out: MdInline[] = [];
  const push = (span: MdInline) => {
    const prev = out[out.length - 1];
    if (span.kind === 'text' && prev?.kind === 'text') prev.text += span.text;
    else if (span.text) out.push(span);
  };
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    const index = m.index ?? 0;
    if (index > last) push({ kind: 'text', text: text.slice(last, index) });
    const token = m[0];
    if (m[1]) push({ kind: 'code', text: token.slice(1, -1) });
    else if (m[2]) {
      const href = safeHref(m[4]);
      push(href ? { kind: 'link', text: m[3], href } : { kind: 'text', text: m[3] });
    } else if (m[5]) push({ kind: 'strong', text: token.slice(2, -2) });
    else if (m[6]) push({ kind: 'del', text: token.slice(2, -2) });
    else if (m[7]) push({ kind: 'em', text: token.slice(1, -1) });
    else {
      const href = safeHref(token);
      push(href ? { kind: 'link', text: token, href } : { kind: 'text', text: token });
    }
    last = index + token.length;
  }
  if (last < text.length) push({ kind: 'text', text: text.slice(last) });
  return out;
}

const FENCE = /^\s*(```+|~~~+)\s*([\w#+.-]*)\s*$/;
const BULLET = /^\s*[-*+•]\s+(.*)$/;
const NUMBERED = /^\s*(\d+)[.)]\s+(.*)$/;
const HEADING = /^\s*(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^\s*>\s?(.*)$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

function cells(line: string): string[] {
  let row = line.trim();
  if (row.startsWith('|')) row = row.slice(1);
  if (row.endsWith('|') && !row.endsWith('\\|')) row = row.slice(0, -1);
  return row.split(/(?<!\\)\|/).map((cell) => cell.replace(/\\\|/g, '|').trim());
}

function alignOf(cell: string): MdAlign {
  const left = cell.startsWith(':'), right = cell.endsWith(':');
  return left && right ? 'center' : right ? 'right' : left ? 'left' : null;
}

/**
 * Blocks of an answer: headings, paragraphs, bullet and numbered lists, fenced code with
 * its language, pipe tables, quotes and rules. An unclosed fence (a reply still
 * streaming) runs to the end, so code shows as code while it arrives.
 */
export function parseMarkdown(source: string): MdBlock[] {
  const blocks: MdBlock[] = [];
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  let paragraph: string[] = [];
  let list: { ordered: boolean; start: number; items: string[] } | null = null;
  let quote: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ kind: 'paragraph', inline: parseMdInline(paragraph.join(' ')) });
    if (list) blocks.push({ kind: 'list', ordered: list.ordered, start: list.start, items: list.items.map(parseMdInline) });
    if (quote.length) blocks.push({ kind: 'quote', inline: parseMdInline(quote.join(' ')) });
    paragraph = [];
    list = null;
    quote = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = FENCE.exec(line);
    if (fence) {
      flush();
      const marker = fence[1];
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].trim().startsWith(marker); i++) code.push(lines[i]);
      blocks.push({ kind: 'code', lang: fence[2].toLowerCase(), text: code.join('\n') });
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      const header = cells(line);
      const separator = cells(lines[i + 1]);
      if (separator.length === header.length) {
        flush();
        const rows: MdInline[][][] = [];
        for (i += 2; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) {
          const row = cells(lines[i]);
          rows.push(header.map((_, c) => parseMdInline(row[c] ?? '')));
        }
        i--;
        blocks.push({ kind: 'table', header: header.map(parseMdInline), align: separator.map(alignOf), rows });
        continue;
      }
    }
    if (RULE.test(line)) { flush(); blocks.push({ kind: 'rule' }); continue; }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      blocks.push({ kind: 'heading', level: Math.min(heading[1].length, 3) as 1 | 2 | 3, inline: parseMdInline(heading[2]) });
      continue;
    }
    const quoted = QUOTE.exec(line);
    if (quoted) {
      if (paragraph.length || list) flush();
      quote.push(quoted[1].trim());
      continue;
    }
    const bullet = BULLET.exec(line), numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      const ordered = Boolean(numbered);
      if (paragraph.length || quote.length || (list && list.ordered !== ordered)) flush();
      list ??= { ordered, start: numbered ? Number(numbered[1]) : 1, items: [] };
      list.items.push((bullet ? bullet[1] : numbered![2]).trim());
      continue;
    }
    if (list && /^\s+\S/.test(line)) { list.items[list.items.length - 1] += ` ${line.trim()}`; continue; }
    if (list || quote.length) flush();
    paragraph.push(line.trim());
  }
  flush();
  return blocks;
}

function inlineText(parts: MdInline[]): string {
  return parts.map((p) => (p.kind === 'link' && p.text !== p.href ? `${p.text} (${p.href})` : p.text)).join('');
}

/** The answer as plain text ("Copy as text"): no Markdown marks, lists and tables kept readable. */
export function markdownToPlainText(source: string): string {
  return parseMarkdown(source).map((block) => {
    switch (block.kind) {
      case 'heading': case 'paragraph': return inlineText(block.inline);
      case 'quote': return inlineText(block.inline);
      case 'list': return block.items.map((item, i) => `${block.ordered ? `${block.start + i}.` : '•'} ${inlineText(item)}`).join('\n');
      case 'code': return block.text;
      case 'table': return [block.header, ...block.rows].map((row) => row.map(inlineText).join('\t')).join('\n');
      case 'rule': return '';
    }
  }).filter((text, i, all) => text !== '' || (i > 0 && all[i - 1] !== '')).join('\n\n').trim();
}
