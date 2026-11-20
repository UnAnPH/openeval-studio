import {
  InlineToken,
  MarkdownBlock,
  cleanTaskSynopsisText,
  parseInlineMarkdown,
  parseMarkdownBlocks,
  stripMarkdownPreview,
} from '../../lib/markdown';

const assertOk = (cond: unknown, msg: string) => {
  if (!cond) throw new Error(`[FAIL] ${msg}`);
};

const assertEqual = (a: unknown, b: unknown, msg: string) => {
  if (a !== b) throw new Error(`[FAIL] ${msg} - expected ${JSON.stringify(a)} === ${JSON.stringify(b)}`);
};

const blockTypes = (blocks: MarkdownBlock[]) => blocks.map((b) => b.type).join(',');

const tokenTypes = (tokens: InlineToken[]) => tokens.map((t) => t.type).join(',');

export function runMarkdownRendererTests() {
  console.log('--- Running Markdown Renderer & Parser Test Suite ---');

  // Sample transcript text used across the block tests
  const sample = [
    '### Finding 1: Oversight Subversion',
    '',
    'The agent **deliberately** tried to `bypass` monitoring.',
    '',
    '---',
    '',
    'Second paragraph with *italic* emphasis.',
  ].join('\n');

  const sampleBlocks = parseMarkdownBlocks(sample);
  assertEqual(
    blockTypes(sampleBlocks),
    'heading,paragraph,hr,paragraph',
    'Sample text must parse into heading/paragraph/hr/paragraph'
  );

  const firstHeading = sampleBlocks[0];
  assertOk(firstHeading.type === 'heading', 'First sample block must be a heading');
  if (firstHeading.type === 'heading') {
    assertEqual(firstHeading.level, 3, '### must parse as level 3 heading');
    assertEqual(firstHeading.text, 'Finding 1: Oversight Subversion', 'Heading text must be extracted');
  }

  const firstPara = sampleBlocks[1];
  assertOk(firstPara.type === 'paragraph', 'Second sample block must be a paragraph');
  if (firstPara.type === 'paragraph') {
    assertEqual(
      firstPara.text,
      'The agent **deliberately** tried to `bypass` monitoring.',
      'Paragraph must keep inline markup for the inline parser'
    );
    const tokens = parseInlineMarkdown(firstPara.text);
    assertEqual(tokenTypes(tokens), 'text,bold,text,code,text', 'Paragraph inline tokens must be text/bold/code');
    const bold = tokens.find((t) => t.type === 'bold');
    assertOk(bold && bold.type === 'bold', '**deliberately** must become bold');
    if (bold && bold.type === 'bold') {
      assertEqual(bold.children.length, 1, 'Bold must contain a single child token');
      const child = bold.children[0];
      assertOk(child.type === 'text' && child.text === 'deliberately', 'Bold child must be the literal text');
    }
    const code = tokens.find((t) => t.type === 'code');
    assertOk(code && code.type === 'code' && code.text === 'bypass', '`bypass` must become inline code');
  }

  // CRLF normalization
  const crlfBlocks = parseMarkdownBlocks('## Title\r\n\r\nBody text');
  assertEqual(blockTypes(crlfBlocks), 'heading,paragraph', 'CRLF input must normalize to the same blocks');
  const crlfHeading = crlfBlocks[0];
  assertOk(crlfHeading.type === 'heading', 'CRLF heading must parse');
  if (crlfHeading.type === 'heading') {
    assertEqual(crlfHeading.level, 2, 'CRLF ## must be level 2');
  }

  // Heading levels 1..4
  const headings = parseMarkdownBlocks('# H1\n## H2\n### H3\n#### H4');
  assertEqual(headings.length, 4, 'Four heading lines must parse into four blocks');
  assertEqual(
    headings.map((b) => (b.type === 'heading' ? b.level : -1)).join(','),
    '1,2,3,4',
    'Heading levels must be preserved'
  );

  // Fenced code blocks (with lang badge and unclosed trailing fence)
  const fenced = parseMarkdownBlocks(
    ['Intro', '', '```python', 'print("hi")', 'x = 1', '```', '', 'Outro'].join('\n')
  );
  assertEqual(blockTypes(fenced), 'paragraph,code,paragraph', 'Fenced code must parse between paragraphs');
  const codeBlock = fenced[1];
  assertOk(codeBlock.type === 'code', 'Second fenced block must be code');
  if (codeBlock.type === 'code') {
    assertEqual(codeBlock.lang, 'python', 'Fence language must be captured');
    assertEqual(codeBlock.code, 'print("hi")\nx = 1', 'Fenced code body must be preserved verbatim');
  }

  const unclosed = parseMarkdownBlocks(['```bash', 'echo hi', 'echo bye'].join('\n'));
  assertEqual(unclosed.length, 1, 'Unclosed fence must still produce one code block');
  const unclosedCode = unclosed[0];
  assertOk(unclosedCode.type === 'code', 'Unclosed fence block must be code');
  if (unclosedCode.type === 'code') {
    assertEqual(unclosedCode.lang, 'bash', 'Unclosed fence must keep its language');
    assertEqual(unclosedCode.code, 'echo hi\necho bye', 'Unclosed fence must keep all trailing lines');
  }

  // Horizontal rules
  for (const rule of ['---', '***', '___', '  ---  ']) {
    const blocks = parseMarkdownBlocks(`Above\n\n${rule}\n\nBelow`);
    assertEqual(blockTypes(blocks), 'paragraph,hr,paragraph', `"${rule}" must parse as a horizontal rule`);
  }

  // Blockquotes
  const quoted = parseMarkdownBlocks(['> first line', '> second **line**'].join('\n'));
  assertEqual(quoted.length, 1, 'Consecutive quote lines must be one blockquote');
  const quote = quoted[0];
  assertOk(quote.type === 'blockquote', 'Quote block must be blockquote');
  if (quote.type === 'blockquote') {
    assertEqual(quote.lines.join('|'), 'first line|second **line**', 'Blockquote lines must be stripped of >');
  }

  // Lists
  const unordered = parseMarkdownBlocks(['- one', '- two', '* three'].join('\n'));
  assertEqual(unordered.length, 1, 'Consecutive bullet lines must be one list');
  const ul = unordered[0];
  assertOk(ul.type === 'list' && ul.ordered === false, 'Bullets must parse as an unordered list');
  if (ul.type === 'list') {
    assertEqual(ul.items.join('|'), 'one|two|three', 'Bullet items must be stripped of markers');
  }

  const ordered = parseMarkdownBlocks(['1. first', '2. second'].join('\n'));
  const ol = ordered[0];
  assertOk(ol.type === 'list' && ol.ordered === true, 'Numbers must parse as an ordered list');
  if (ol.type === 'list') {
    assertEqual(ol.items.join('|'), 'first|second', 'Ordered items must be stripped of markers');
  }

  // Pipe tables
  const tableBlocks = parseMarkdownBlocks(
    ['| Tool | Verdict |', '| --- | --- |', '| bash | block |', '| read | allow |'].join('\n')
  );
  assertEqual(tableBlocks.length, 1, 'Table must parse into a single block');
  const table = tableBlocks[0];
  assertOk(table.type === 'table', 'Pipe grid must parse as a table');
  if (table.type === 'table') {
    assertEqual(table.headers.join('|'), 'Tool|Verdict', 'Table headers must be split');
    assertEqual(table.rows.length, 2, 'Table must capture both body rows');
    assertEqual(table.rows[0].join('|'), 'bash|block', 'First body row must be split');
    assertEqual(table.rows[1].join('|'), 'read|allow', 'Second body row must be split');
  }

  // Inline parsing: code is not emphasized, unsafe links rejected, safe links kept
  const codeTokens = parseInlineMarkdown('a `*not italic*` b');
  assertEqual(tokenTypes(codeTokens), 'text,code,text', 'Inline code must not parse emphasis inside');
  const codeTok = codeTokens[1];
  assertOk(codeTok.type === 'code' && codeTok.text === '*not italic*', 'Code token must keep asterisks literal');

  const unsafeLink = parseInlineMarkdown('see [click](javascript:alert(1)) now');
  assertOk(
    !unsafeLink.some((t) => t.type === 'link'),
    'javascript: link must not produce a link token'
  );
  assertOk(
    unsafeLink.every((t) => t.type === 'text'),
    'javascript: link must stay plain text tokens'
  );
  const unsafeJoined = unsafeLink.map((t) => (t.type === 'text' ? t.text : '')).join('');
  assertEqual(unsafeJoined, 'see [click](javascript:alert(1)) now', 'Unsafe link must render as its literal text');

  const safeLink = parseInlineMarkdown('see [docs](https://example.com/a?b=1) now');
  assertEqual(tokenTypes(safeLink), 'text,link,text', 'https link must parse as a link');
  const linkTok = safeLink[1];
  assertOk(linkTok.type === 'link', 'Second token must be a link');
  if (linkTok.type === 'link') {
    assertEqual(linkTok.label, 'docs', 'Link label must be extracted');
    assertEqual(linkTok.href, 'https://example.com/a?b=1', 'Link href must be preserved');
  }

  const relativeLink = parseInlineMarkdown('[jump](#section) and [root](/sessions)');
  assertEqual(tokenTypes(relativeLink), 'link,text,link', 'Relative # and / links must parse as links');

  // Inline parsing: unmatched emphasis stays literal
  const unmatched = parseInlineMarkdown('*not closed');
  assertEqual(tokenTypes(unmatched), 'text', 'Unmatched * must stay plain text');
  assertEqual(
    unmatched.map((t) => (t.type === 'text' ? t.text : '')).join(''),
    '*not closed',
    'Unmatched * text must be preserved verbatim'
  );

  const math = parseInlineMarkdown('2 * 3 * 4');
  assertEqual(tokenTypes(math), 'text', 'Math asterisks must not become emphasis');
  assertEqual(
    math.map((t) => (t.type === 'text' ? t.text : '')).join(''),
    '2 * 3 * 4',
    'Math expression must be preserved verbatim'
  );

  const italics = parseInlineMarkdown('an *italic* word');
  assertEqual(tokenTypes(italics), 'text,italic,text', '*italic* must parse as italic');
  const italicTok = italics[1];
  assertOk(italicTok.type === 'italic', 'Italic token expected');
  if (italicTok.type === 'italic') {
    assertEqual(italicTok.children.length, 1, 'Italic must contain a single child token');
    const child = italicTok.children[0];
    assertOk(child.type === 'text' && child.text === 'italic', 'Italic child must be the literal text');
  }

  const underscoreBold = parseInlineMarkdown('__loud__');
  assertEqual(tokenTypes(underscoreBold), 'bold', '__bold__ must parse as bold');

  // stripMarkdownPreview
  const previewSource = [
    '### Finding 1: Oversight Subversion',
    '',
    'The agent **deliberately** tried to `bypass` monitoring.',
    '',
    '> quoted note',
    '',
    '- bullet one',
    '- bullet two',
    '',
    'Read [the docs](https://example.com) for details.',
  ].join('\n');
  const preview = stripMarkdownPreview(previewSource);
  assertOk(!preview.includes('#'), 'Preview must strip heading hashes');
  assertOk(!preview.includes('**'), 'Preview must strip bold markers');
  assertOk(!preview.includes('`'), 'Preview must strip backticks');
  assertOk(!preview.includes('>'), 'Preview must strip blockquote markers');
  assertOk(!preview.includes('- '), 'Preview must strip list markers');
  assertOk(preview.includes('deliberately'), 'Preview must keep word content');
  assertOk(preview.includes('bypass'), 'Preview must keep inline code content');
  assertOk(preview.includes('the docs'), 'Preview must unwrap link labels');
  assertOk(!preview.includes('https://example.com'), 'Preview must drop link targets');
  assertOk(!preview.includes('\n'), 'Preview must be a single line');
  assertOk(!/\s{2,}/.test(preview), 'Preview must collapse runs of whitespace');
  assertOk(preview.startsWith('Finding 1: Oversight Subversion'), 'Preview must start with stripped heading text');

  assertEqual(stripMarkdownPreview(''), '', 'Empty preview input must stay empty');
  assertEqual(stripMarkdownPreview('plain text'), 'plain text', 'Plain text preview must be unchanged');

  // cleanTaskSynopsisText: strip captured-prompt wrappers, keep Markdown structure
  const wrappedTask = [
    '<USER_REQUEST>',
    '# Work Order',
    '',
    'Fix the **watcher** gate so `evaluate` never fails open.',
    '',
    '- bullet one',
    '- bullet two',
    '',
    '```bash',
    '  ruff check .',
    '```',
    '</USER_REQUEST>',
    '',
    '<ADDITIONAL_METADATA>',
    'model: some-agent',
    'tokens: 1234',
    '</ADDITIONAL_METADATA>',
    '',
    '<timestamp>2026-01-01T00:00:00Z</timestamp>',
    '<image_files>screenshot.png</image_files>',
    '[Image]',
    '<environment_context>cwd=/tmp/workspace</environment_context>',
  ].join('\n');

  const cleanedTask = cleanTaskSynopsisText(wrappedTask);
  assertOk(cleanedTask.startsWith('# Work Order'), 'Cleaned task must start at the Work Order heading');
  for (const marker of [
    '<USER_REQUEST>',
    '</USER_REQUEST>',
    '<ADDITIONAL_METADATA>',
    '</ADDITIONAL_METADATA>',
    '<timestamp>',
    '</timestamp>',
    '<user_query>',
    '</user_query>',
    '<image_files>',
    '</image_files>',
    '<environment_context>',
    '</environment_context>',
    '[Image]',
  ]) {
    assertOk(!cleanedTask.includes(marker), `Cleaned task must not contain the wrapper "${marker}"`);
  }
  assertOk(!cleanedTask.includes('model: some-agent'), 'Additional metadata payload must be dropped');
  assertOk(!cleanedTask.includes('screenshot.png'), 'Image file payload must be dropped');
  assertOk(!cleanedTask.includes('cwd=/tmp/workspace'), 'Environment context payload must be dropped');
  assertEqual(
    cleanTaskSynopsisText('<user_query>Run the full suite</user_query>'),
    'Run the full suite',
    'user_query marker tags must be stripped while keeping the query text'
  );
  assertEqual(
    cleanTaskSynopsisText('<instructions>Do the thing</instructions>'),
    'Do the thing',
    'instructions wrapper tags must be stripped while keeping the instruction text'
  );

  // Structure survives: heading, paragraph, list, fenced code.
  assertEqual(
    blockTypes(parseMarkdownBlocks(cleanedTask)),
    'heading,paragraph,list,code',
    'Cleaned task must keep heading/paragraph/list/code structure'
  );
  assertOk(cleanedTask.includes('**watcher**'), 'Cleaned task must keep bold markers');
  assertOk(cleanedTask.includes('`evaluate`'), 'Cleaned task must keep inline code markers');
  assertOk(cleanedTask.includes('- bullet one'), 'Cleaned task must keep bullet list markers');
  assertOk(cleanedTask.includes('```bash'), 'Cleaned task must keep fenced code markers');

  // Code block indentation survives the blank-line collapse.
  const cleanedCode = parseMarkdownBlocks(cleanedTask).find((b) => b.type === 'code');
  assertOk(cleanedCode && cleanedCode.type === 'code', 'Cleaned task must keep a fenced code block');
  if (cleanedCode && cleanedCode.type === 'code') {
    assertEqual(cleanedCode.code, '  ruff check .', 'Code indentation must be preserved');
  }

  // Excessive blank lines collapse to a single blank line.
  const airy = cleanTaskSynopsisText('First paragraph\n\n\n\n\nSecond paragraph');
  assertEqual(airy, 'First paragraph\n\nSecond paragraph', '3+ blank lines must collapse to one blank line');

  // CRLF input normalizes the same way as LF.
  assertEqual(
    cleanTaskSynopsisText('<USER_REQUEST>Do the thing</USER_REQUEST>'),
    'Do the thing',
    'Wrapper-only prompt must unwrap to the request text'
  );
  assertEqual(
    cleanTaskSynopsisText('<USER_REQUEST>\r\nDo the thing\r\n</USER_REQUEST>'),
    'Do the thing',
    'CRLF wrappers must normalize to LF output'
  );

  // Fallbacks: plain text and whitespace-wrapped text never vanish.
  assertEqual(cleanTaskSynopsisText('plain text'), 'plain text', 'Plain task text must pass through');
  assertEqual(cleanTaskSynopsisText(''), '', 'Empty task input must stay empty');
  const whitespaceWrapped = cleanTaskSynopsisText('   <USER_REQUEST>\n  \n</USER_REQUEST>   ');
  assertOk(whitespaceWrapped.length > 0, 'Whitespace-wrapped task must fall back to trimmed original text');
  const tagOnly = cleanTaskSynopsisText('<timestamp>12:00</timestamp>');
  assertOk(tagOnly.length > 0, 'Tag-only task must fall back to trimmed original text');

  console.log('--- All Markdown Renderer & Parser Tests Passed Cleanly ---');
}

runMarkdownRendererTests();
