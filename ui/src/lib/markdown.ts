/**
 * Zero-dependency Markdown parser for transcript rendering.
 *
 * Deliberately pure TypeScript (no React, no DOM) so the Node `tsx` test suite
 * can exercise it directly. `MarkdownRenderer.tsx` owns the JSX rendering and
 * never uses `dangerouslySetInnerHTML`, so untrusted transcript text can never
 * inject markup or script.
 */

export type InlineToken =
  | { type: 'text'; text: string }
  | { type: 'code'; text: string }
  | { type: 'bold'; children: InlineToken[] }
  | { type: 'italic'; children: InlineToken[] }
  | { type: 'link'; label: string; href: string };

export type MarkdownBlock =
  | { type: 'heading'; level: 1 | 2 | 3 | 4; text: string }
  | { type: 'hr' }
  | { type: 'code'; lang: string; code: string }
  | { type: 'blockquote'; lines: string[] }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'table'; headers: string[]; rows: string[][] }
  | { type: 'paragraph'; text: string };

/** Only absolute http(s) and same-document/root-relative targets are linkable. */
const SAFE_HREF = /^(?:https?:\/\/|\/|#)/i;

export function isSafeHref(href: string): boolean {
  return SAFE_HREF.test(href.trim());
}

// Inline alternation, ordered by precedence: code first (so `*` inside code is
// never treated as emphasis), then links, then bold, then italic. The emphasis
// bodies are bounded and non-greedy, so an unmatched `*` (e.g. `2 * 3` or
// `*not closed`) simply stays literal text.
const INLINE_PATTERN =
  /`([^`\n]+)`|\[([^\]\n]+)\]\(([^)\s]+)\)|\*\*([^*\n]+?)\*\*|__([^_\n]+?)__|\*(?![\s*])([^*\n]+?)\*/g;

export function parseInlineMarkdown(text: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  if (!text) return tokens;

  const pattern = new RegExp(INLINE_PATTERN.source, 'g');
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    const raw = match[0];
    if (match.index > lastIndex) {
      tokens.push({ type: 'text', text: text.slice(lastIndex, match.index) });
    }

    const [, code, label, href, boldStar, boldUnderscore, italicStar] = match;

    if (code !== undefined) {
      tokens.push({ type: 'code', text: code });
    } else if (label !== undefined && href !== undefined) {
      if (isSafeHref(href)) {
        tokens.push({ type: 'link', label, href: href.trim() });
      } else {
        // Unsafe scheme (javascript:, data:, vbscript:, ...) stays inert text.
        tokens.push({ type: 'text', text: raw });
      }
    } else if (boldStar !== undefined) {
      tokens.push({ type: 'bold', children: parseInlineMarkdown(boldStar) });
    } else if (boldUnderscore !== undefined) {
      tokens.push({ type: 'bold', children: parseInlineMarkdown(boldUnderscore) });
    } else if (italicStar !== undefined) {
      tokens.push({ type: 'italic', children: parseInlineMarkdown(italicStar) });
    } else {
      tokens.push({ type: 'text', text: raw });
    }

    lastIndex = match.index + raw.length;
    if (raw.length === 0) pattern.lastIndex += 1;
  }

  if (lastIndex < text.length) {
    tokens.push({ type: 'text', text: text.slice(lastIndex) });
  }

  return tokens;
}

const FENCE_OPEN = /^\s*```\s*([A-Za-z0-9_+-]*)\s*$/;
const FENCE_CLOSE = /^\s*```\s*$/;
const HEADING = /^(#{1,4})\s+(.+?)\s*$/;
const HR = /^\s*([-*_])(?:\s*\1){2,}\s*$/;
const BLOCKQUOTE = /^\s*>\s?/;
const ORDERED_ITEM = /^\s*\d+\.\s+(.*)$/;
const UNORDERED_ITEM = /^\s*[-*]\s+(.*)$/;

function isBlank(line: string): boolean {
  return line.trim().length === 0;
}

function splitTableRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith('|')) row = row.slice(1);
  if (row.endsWith('|')) row = row.slice(0, -1);
  return row.split('|').map((cell) => cell.trim());
}

function isTableDivider(line: string): boolean {
  if (!line.includes('|')) return false;
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell));
}

/** True when `lines[i]` begins a block that must terminate a paragraph. */
function startsNewBlock(lines: string[], i: number): boolean {
  const line = lines[i];
  if (FENCE_OPEN.test(line)) return true;
  if (HEADING.test(line)) return true;
  if (HR.test(line)) return true;
  if (BLOCKQUOTE.test(line)) return true;
  if (ORDERED_ITEM.test(line) || UNORDERED_ITEM.test(line)) return true;
  if (line.includes('|') && i + 1 < lines.length && isTableDivider(lines[i + 1])) return true;
  return false;
}

export function parseMarkdownBlocks(markdown: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  if (!markdown) return blocks;

  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (isBlank(line)) {
      i += 1;
      continue;
    }

    // Fenced code block (tolerates an unclosed trailing fence).
    const fence = line.match(FENCE_OPEN);
    if (fence) {
      const lang = fence[1] || '';
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE_CLOSE.test(lines[i])) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1; // consume closing fence when present
      blocks.push({ type: 'code', lang, code: codeLines.join('\n') });
      continue;
    }

    // ATX heading (# .. ####).
    const heading = line.match(HEADING);
    if (heading) {
      blocks.push({
        type: 'heading',
        level: heading[1].length as 1 | 2 | 3 | 4,
        text: heading[2].trim(),
      });
      i += 1;
      continue;
    }

    // Horizontal rule (---, ***, ___).
    if (HR.test(line)) {
      blocks.push({ type: 'hr' });
      i += 1;
      continue;
    }

    // Blockquote.
    if (BLOCKQUOTE.test(line)) {
      const quoteLines: string[] = [];
      while (i < lines.length && BLOCKQUOTE.test(lines[i])) {
        quoteLines.push(lines[i].replace(BLOCKQUOTE, ''));
        i += 1;
      }
      blocks.push({ type: 'blockquote', lines: quoteLines });
      continue;
    }

    // Pipe table (header row + divider row + body rows).
    if (line.includes('|') && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
      const headers = splitTableRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && !isBlank(lines[i]) && lines[i].includes('|')) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      blocks.push({ type: 'table', headers, rows });
      continue;
    }

    // Ordered / unordered list.
    if (ORDERED_ITEM.test(line) || UNORDERED_ITEM.test(line)) {
      const ordered = ORDERED_ITEM.test(line);
      const itemPattern = ordered ? ORDERED_ITEM : UNORDERED_ITEM;
      const items: string[] = [];
      while (i < lines.length) {
        const item = lines[i].match(itemPattern);
        if (!item) break;
        items.push(item[1].trim());
        i += 1;
      }
      blocks.push({ type: 'list', ordered, items });
      continue;
    }

    // Paragraph: consecutive non-empty lines until the next block starts.
    const paraLines: string[] = [];
    while (i < lines.length) {
      if (isBlank(lines[i])) break;
      if (paraLines.length > 0 && startsNewBlock(lines, i)) break;
      paraLines.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ type: 'paragraph', text: paraLines.join(' ') });
  }

  return blocks;
}

/** Paired wrapper blocks whose payload is machine metadata: drop both. */
const TASK_BLOCK_WRAPPERS = [
  'ADDITIONAL_METADATA',
  'environment_context',
  'image_files',
  'timestamp',
];

/** Delimiter tags that only wrap human-readable prose: strip the tags, keep the text. */
const TASK_MARKER_WRAPPERS = [
  'USER_REQUEST',
  'user_query',
  'instructions',
  'system_instruction',
];

/**
 * Cleans a captured agent prompt for human display in the Session Synopsis.
 *
 * Agent transcripts wrap the human's request in XML-ish envelopes
 * (`<USER_REQUEST>`, `<ADDITIONAL_METADATA>`, `<environment_context>`, …) and
 * sprinkle `[Image]` placeholders through them. This strips those wrappers while
 * preserving the Markdown structure of whatever prose remains, so the synopsis
 * can be rendered by `MarkdownRenderer`.
 */
export function cleanTaskSynopsisText(text: string): string {
  if (!text) return '';
  let cleaned = text.replace(/\r\n/g, '\n');

  // Paired blocks: drop the wrapper *and* its payload.
  for (const tag of TASK_BLOCK_WRAPPERS) {
    const paired = new RegExp(`<${tag}(?:\\s[^>]*)?>[\\s\\S]*?<\\/${tag}\\s*>`, 'gi');
    cleaned = cleaned.replace(paired, '\n');
  }

  // Unclosed metadata trailer: everything after it is machine payload, not task.
  const orphanMetadata = cleaned.search(/<ADDITIONAL_METADATA(?:\s[^>]*)?>/i);
  if (orphanMetadata !== -1) {
    cleaned = cleaned.slice(0, orphanMetadata);
  }

  // Any remaining standalone wrapper tags (openers, closers, self-closing).
  const allWrappers = [...TASK_BLOCK_WRAPPERS, ...TASK_MARKER_WRAPPERS];
  for (const tag of allWrappers) {
    const stray = new RegExp(`<\\/?${tag}(?:\\s[^>]*)?\\/?>`, 'gi');
    cleaned = cleaned.replace(stray, '\n');
  }

  // `[Image]` attachment placeholders: whole lines first, then inline tokens.
  cleaned = cleaned
    .replace(/^[ \t]*\[Image\][ \t]*$/gim, '\n')
    .replace(/\[Image\]/gi, ' ');

  // Tidy whitespace without touching Markdown structure or code indentation.
  cleaned = cleaned
    .replace(/[ \t]+$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  // Never let a title/task silently vanish because of aggressive stripping.
  if (cleaned) return cleaned;

  return text.replace(/\r\n/g, '\n').trim();
}

/**
 * Single-line preview text for collapsed message rows: strips structural
 * markers and collapses whitespace.
 */
export function stripMarkdownPreview(text: string): string {
  if (!text) return '';
  return text
    .replace(/\r\n/g, '\n')
    .replace(/```[^\n]*\n?/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*([-*_])(?:\s*\1){2,}\s*$/gm, ' ')
    .replace(/^\s*[-*]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/^\s*\|?[\s:|-]+\|[\s:|-]*$/gm, ' ')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/\|/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}
