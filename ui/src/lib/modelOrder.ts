import { ModelSpec } from '../types';

const LAUNCH_MODEL_PRIORITY: Record<string, number> = {
  // Google Gemini family (Gemini 4 -> Gemini 3.8..3 -> Gemma 4)
  'gemini-4-hydrogen': 10,
  'gemini-3.8-flash': 20,
  'gemini-3.6-flash': 21,
  'gemini-3.5-flash-lite': 22,
  'gemini-3.1-flash-lite': 23,
  'gemini-3-flash-preview': 24,
  'gemma-4-31b-it': 30,
  'gemma-4-26b-it': 31,

  // OpenCode Go (grouped by family, alphabetical)
  'opencode/claude-haiku-5-5': 100,
  'opencode/deepseek-v4.1-flash': 110,
  'opencode/deepseek-v4-pro': 111,
  'opencode/gemini-4-argon': 120,
  'opencode/glm-5.3-flash': 130,
  'opencode/glm-5.3': 131,
  'opencode/gpt-6-luna': 140,
  'opencode/grok-4.7': 150,
  'opencode/kimi-k3': 160,
  'opencode/kimi-k3.1-pro': 161,
  'opencode/minimax-m3': 170,
  'opencode/minimax-m3.1-flash': 171,
  'opencode/qwen3.8-flash': 180,
  'opencode/qwen3.8-max': 181,
};

function extractFamilyKey(model: Pick<ModelSpec, 'id' | 'name'>): string {
  const cleanName = model.name.replace(/\s*\(Go\)$/i, '').trim().toLowerCase();
  const [family] = cleanName.split(/[\s-]+/);
  return `${family}::${cleanName}::${model.id}`;
}

export function sortModelsByFamily<T extends Pick<ModelSpec, 'id' | 'name'>>(models: T[]): T[] {
  return [...models].sort((a, b) => {
    const rankA = LAUNCH_MODEL_PRIORITY[a.id];
    const rankB = LAUNCH_MODEL_PRIORITY[b.id];
    if (rankA !== undefined && rankB !== undefined) return rankA - rankB;
    if (rankA !== undefined) return -1;
    if (rankB !== undefined) return 1;
    return extractFamilyKey(a).localeCompare(extractFamilyKey(b), undefined, { numeric: true });
  });
}
