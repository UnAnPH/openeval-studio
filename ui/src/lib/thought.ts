/**
 * Normalize agent step reasoning text for display.
 *
 * Strips empty/stray think tags and markdown code fences, and unwraps
 * serialized ReAct JSON action payloads (e.g. `{"thought": "...", "tool": ...}`)
 * when a model fallback or streamed chunk stores raw action JSON inside `step.thought`.
 */
export function cleanStepThought(raw?: string | null): string {
  if (!raw) return '';
  let current = raw.trim();

  for (let depth = 0; depth < 3; depth += 1) {
    current = current
      .replace(/<think>\s*<\/think>/gi, '')
      .replace(/<\/?thinking?>/gi, '')
      .replace(/^```(?:json|text|markdown)?\s*\n?/i, '')
      .replace(/\n?```\s*$/i, '')
      .trim();

    if (!current.includes('{')) {
      break;
    }

    let unwrapped: string | null = null;
    const firstBrace = current.indexOf('{');
    const lastBrace = current.lastIndexOf('}');

    if (firstBrace !== -1 && lastBrace > firstBrace) {
      const candidateSlice = current.slice(firstBrace, lastBrace + 1);
      try {
        const parsed = JSON.parse(candidateSlice);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          if (typeof parsed.thought === 'string' && parsed.thought.trim()) {
            unwrapped = parsed.thought.trim();
          } else if (typeof parsed.summary === 'string' && parsed.summary.trim() && 'tool' in parsed) {
            unwrapped = parsed.summary.trim();
          }
        }
      } catch {
        // Ignore malformed JSON; fall through to partial-JSON extraction below.
      }
    }

    if (unwrapped === null && /^\s*\{/.test(current) && /"(?:tool|command|path|summary)"\s*:/.test(current)) {
      const match = current.match(/"thought"\s*:\s*"((?:\\.|[^"\\])*)"/);
      if (match && match[1]) {
        try {
          unwrapped = JSON.parse(`"${match[1]}"`).trim();
        } catch {
          unwrapped = match[1].replace(/\\n/g, '\n').replace(/\\"/g, '"').replace(/\\\\/g, '\\').trim();
        }
      }
    }

    if (unwrapped === null || unwrapped === current) {
      break;
    }
    current = unwrapped;
  }

  return current
    .replace(/^```(?:json|text|markdown)?\s*\n?/i, '')
    .replace(/\n?```\s*$/i, '')
    .trim();
}
