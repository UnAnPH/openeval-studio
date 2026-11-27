import React from 'react';
import {
  InlineToken,
  MarkdownBlock,
  parseInlineMarkdown,
  parseMarkdownBlocks,
} from '../lib/markdown';

type Tone = 'slate' | 'purple' | 'dark' | 'emerald';

interface MarkdownRendererProps {
  content: string;
  renderText?: (text: string) => React.ReactNode;
  tone?: Tone;
  className?: string;
}

interface ToneStyle {
  root: string;
  strong: string;
  muted: string;
  rule: string;
  inlineCode: string;
  link: string;
  quote: string;
  tableHead: string;
  tableBorder: string;
  tableRowBorder: string;
}

const TONE_STYLES: Record<Tone, ToneStyle> = {
  slate: {
    root: 'text-xs leading-relaxed text-slate-700',
    strong: 'text-slate-900',
    muted: 'text-slate-600',
    rule: 'border-slate-200',
    inlineCode: 'bg-slate-200/70 text-slate-800 border-slate-300',
    link: 'text-indigo-600 hover:text-indigo-800 decoration-indigo-400',
    quote: 'border-slate-300 bg-slate-100/70 text-slate-600',
    tableHead: 'bg-slate-100 text-slate-800 border-slate-200',
    tableBorder: 'border-slate-200',
    tableRowBorder: 'border-slate-100',
  },
  purple: {
    root: 'text-purple-950',
    strong: 'text-purple-950',
    muted: 'text-purple-700',
    rule: 'border-purple-100',
    inlineCode: 'bg-purple-100/80 text-purple-900 border-purple-200',
    link: 'text-purple-700 hover:text-purple-900 decoration-purple-400',
    quote: 'border-purple-200 bg-purple-50/60 text-purple-900',
    tableHead: 'bg-purple-50 text-purple-900 border-purple-200',
    tableBorder: 'border-purple-200',
    tableRowBorder: 'border-purple-100',
  },
  dark: {
    root: 'text-xs text-slate-200',
    strong: 'text-slate-50',
    muted: 'text-slate-400',
    rule: 'border-slate-800',
    inlineCode: 'bg-slate-800/90 text-indigo-300 border-slate-700',
    link: 'text-indigo-400 hover:text-indigo-300',
    quote: 'border-indigo-400/60 bg-slate-900/70 text-slate-300',
    tableHead: 'bg-slate-900 text-slate-200 border-slate-800',
    tableBorder: 'border-slate-800',
    tableRowBorder: 'border-slate-800/70',
  },
  emerald: {
    root: 'text-xs text-emerald-950',
    strong: 'text-emerald-950',
    muted: 'text-emerald-800',
    rule: 'border-emerald-200/80',
    inlineCode: 'bg-emerald-100/80 text-emerald-900 border-emerald-200',
    link: 'text-emerald-700 hover:text-emerald-900',
    quote: 'border-emerald-300 bg-emerald-50/70 text-emerald-900',
    tableHead: 'bg-emerald-100/60 text-emerald-900 border-emerald-200',
    tableBorder: 'border-emerald-200',
    tableRowBorder: 'border-emerald-200/60',
  },
};

const toneStyles = (tone: Tone): ToneStyle => TONE_STYLES[tone] ?? TONE_STYLES.slate;

const HEADING_CLASSES: Record<1 | 2 | 3 | 4, string> = {
  1: 'text-sm font-bold mt-4 mb-1.5 first:mt-0',
  2: 'text-[13px] font-bold mt-3.5 mb-1.5 first:mt-0',
  3: 'text-xs font-bold mt-3 mb-1 first:mt-0',
  4: 'text-xs font-semibold mt-2.5 mb-1 first:mt-0',
};

/**
 * Renders untrusted transcript Markdown as semantic React elements.
 * No `dangerouslySetInnerHTML`: every string is emitted as a text node.
 */
export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({
  content,
  renderText,
  tone = 'slate',
  className = '',
}) => {
  const styles = toneStyles(tone);

  const renderInline = (tokens: InlineToken[]): React.ReactNode =>
    tokens.map((tok, i) => {
      switch (tok.type) {
        case 'text':
          return (
            <React.Fragment key={i}>
              {renderText ? renderText(tok.text) : tok.text}
            </React.Fragment>
          );
        case 'code':
          return (
            <code
              key={i}
              className={`px-1.5 py-0.5 rounded font-mono text-[11px] border ${styles.inlineCode}`}
            >
              {tok.text}
            </code>
          );
        case 'bold':
          return (
            <strong key={i} className={`font-bold ${styles.strong}`}>
              {renderInline(tok.children)}
            </strong>
          );
        case 'italic':
          return (
            <em key={i} className="italic">
              {renderInline(tok.children)}
            </em>
          );
        case 'link':
          return (
            <a
              key={i}
              href={tok.href}
              target="_blank"
              rel="noopener noreferrer"
              className={`underline underline-offset-2 font-medium break-words ${styles.link}`}
            >
              {renderText ? renderText(tok.label) : tok.label}
            </a>
          );
        default:
          return null;
      }
    });

  const renderBlock = (block: MarkdownBlock, i: number): React.ReactNode => {
    switch (block.type) {
      case 'heading': {
        const Tag = `h${block.level}` as 'h1' | 'h2' | 'h3' | 'h4';
        return (
          <Tag key={i} className={`${HEADING_CLASSES[block.level]} ${styles.strong}`}>
            {renderInline(parseInlineMarkdown(block.text))}
          </Tag>
        );
      }
      case 'hr':
        return <hr key={i} className={`border-t my-3 ${styles.rule}`} />;
      case 'code':
        return (
          <div key={i} className="my-2.5 rounded-xl overflow-hidden bg-slate-900 border border-slate-800">
            {block.lang && (
              <div className="px-3 py-1 text-[10px] font-mono uppercase tracking-wider text-slate-400 bg-slate-950/60 border-b border-slate-800 select-none">
                {block.lang}
              </div>
            )}
            <pre className="p-3 text-slate-100 font-mono text-[11px] leading-relaxed overflow-x-auto whitespace-pre">
              {block.code}
            </pre>
          </div>
        );
      case 'blockquote':
        return (
          <blockquote
            key={i}
            className={`my-2 border-l-2 pl-3 py-1.5 rounded-r-lg italic ${styles.quote}`}
          >
            {block.lines.map((line, li) => (
              <React.Fragment key={li}>
                {li > 0 && <br />}
                {renderInline(parseInlineMarkdown(line))}
              </React.Fragment>
            ))}
          </blockquote>
        );
      case 'list': {
        const ListTag = block.ordered ? 'ol' : 'ul';
        return (
          <ListTag
            key={i}
            className={`my-1.5 pl-5 space-y-1 ${block.ordered ? 'list-decimal' : 'list-disc'}`}
          >
            {block.items.map((item, li) => (
              <li key={li} className="leading-relaxed pl-0.5">
                {renderInline(parseInlineMarkdown(item))}
              </li>
            ))}
          </ListTag>
        );
      }
      case 'table':
        return (
          <div
            key={i}
            className={`my-2.5 overflow-x-auto rounded-xl border ${styles.tableBorder}`}
          >
            <table className="min-w-full text-[11px] border-collapse">
              <thead>
                <tr>
                  {block.headers.map((header, hi) => (
                    <th
                      key={hi}
                      className={`px-3 py-1.5 text-left font-semibold border-b ${styles.tableHead}`}
                    >
                      {renderInline(parseInlineMarkdown(header))}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, ri) => (
                  <tr key={ri}>
                    {row.map((cell, ci) => (
                      <td
                        key={ci}
                        className={`px-3 py-1.5 align-top border-b ${styles.tableRowBorder}`}
                      >
                        {renderInline(parseInlineMarkdown(cell))}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      case 'paragraph':
        return (
          <p key={i} className="my-1.5 leading-relaxed break-words">
            {renderInline(parseInlineMarkdown(block.text))}
          </p>
        );
      default:
        return null;
    }
  };

  return (
    <div className={`font-sans ${styles.root} ${className}`.trim()}>
      {parseMarkdownBlocks(content).map(renderBlock)}
    </div>
  );
};

export default MarkdownRenderer;
