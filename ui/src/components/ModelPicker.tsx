import React from 'react';
import { Bot, DollarSign, Sparkles } from 'lucide-react';
import { ModelSpec } from '../types';

interface ModelPickerProps {
  models: ModelSpec[];
  selectedModelId: string;
  onSelectModel: (modelId: string) => void;
  disabled?: boolean;
}

export const ModelPicker: React.FC<ModelPickerProps> = ({
  models,
  selectedModelId,
  onSelectModel,
  disabled = false,
}) => {
  const selectedModel = models.find((m) => m.id === selectedModelId);

  const getTierBadge = (tier: string) => {
    switch (tier) {
      case 'flagship':
        return 'bg-amber-500/10 text-amber-400 border-amber-500/30';
      case 'fast':
        return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30';
      case 'preview':
        return 'bg-purple-500/10 text-purple-400 border-purple-500/30';
      default:
        return 'bg-sky-500/10 text-sky-400 border-sky-500/30';
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
          <Bot className="w-3.5 h-3.5 text-primary" />
          Evaluation Model
        </label>
        {selectedModel && (
          <span
            className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border ${getTierBadge(
              selectedModel.tier
            )}`}
          >
            {selectedModel.tier}
          </span>
        )}
      </div>

      <div className="relative">
        <select
          value={selectedModelId}
          onChange={(e) => onSelectModel(e.target.value)}
          disabled={disabled}
          className="w-full bg-surface-elevated border border-border rounded-xl px-3.5 py-2.5 text-sm text-slate-100 font-medium focus:outline-none focus:border-primary transition-all disabled:opacity-50 appearance-none cursor-pointer"
        >
          {models.map((m) => (
            <option key={m.id} value={m.id} className="bg-surface text-slate-200">
              {m.name} ({m.provider.toUpperCase()}) — ${m.input_cost_per_m}/1M in, ${m.output_cost_per_m}/1M out
            </option>
          ))}
        </select>
        <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-3 text-slate-400">
          <Sparkles className="w-4 h-4 text-primary" />
        </div>
      </div>

      {selectedModel && (
        <div className="p-3 rounded-xl bg-surface border border-border/80 text-xs space-y-1.5">
          <p className="text-slate-300 leading-relaxed">{selectedModel.description}</p>
          <div className="flex items-center gap-4 text-slate-400 pt-1 border-t border-border/40">
            <span className="flex items-center gap-1">
              <DollarSign className="w-3 h-3 text-emerald-400" />
              In: <strong className="text-slate-200">${selectedModel.input_cost_per_m}</strong> / 1M
            </span>
            <span className="flex items-center gap-1">
              <DollarSign className="w-3 h-3 text-emerald-400" />
              Out: <strong className="text-slate-200">${selectedModel.output_cost_per_m}</strong> / 1M
            </span>
            <span>
              Context: <strong className="text-slate-200">{selectedModel.context_window >= 1_000_000 ? `${selectedModel.context_window / 1_000_000}M` : `${selectedModel.context_window / 1000}k`}</strong>
            </span>
          </div>
        </div>
      )}
    </div>
  );
};
