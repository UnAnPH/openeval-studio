import React from 'react';
import {
  IonIcon,
} from '@ionic/react';
import {
  hardwareChipOutline,
  sparklesOutline,
  cashOutline,
  globeOutline,
} from 'ionicons/icons';
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

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-mono font-bold uppercase tracking-wider text-text-secondary flex items-center gap-1.5">
          <IonIcon icon={hardwareChipOutline} className="text-brand-purple text-sm" />
          Target Evaluation Model
        </label>
        {selectedModel && (
          <span className="text-[10px] uppercase font-mono font-bold px-2 py-0.5 rounded-full bg-surface-subtle text-brand-primary border border-border-subtle">
            {selectedModel.tier}
          </span>
        )}
      </div>

      <div className="relative">
        <select
          value={selectedModelId}
          onChange={(e) => onSelectModel(e.target.value)}
          disabled={disabled}
          className="w-full bg-canvas border border-border-subtle rounded-xl px-3.5 py-2.5 text-xs text-text-primary font-medium focus:outline-none focus:border-brand-primary transition-all disabled:opacity-50 appearance-none cursor-pointer"
        >
          {models.map((m) => (
            <option key={m.id} value={m.id} className="bg-white text-text-primary">
              {m.name} ({m.provider.toUpperCase()}) — ${m.input_cost_per_m}/1M in
            </option>
          ))}
        </select>
        <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-3 text-text-muted">
          <IonIcon icon={sparklesOutline} className="text-accent-orange text-sm" />
        </div>
      </div>

      {selectedModel && (
        <div className="p-3 rounded-xl bg-canvas border border-border-subtle text-xs space-y-2">
          <p className="text-text-secondary leading-relaxed text-xs">{selectedModel.description}</p>
          <div className="flex flex-wrap items-center gap-2 text-text-muted pt-1 border-t border-border-subtle">
            <span className="px-2 py-0.5 rounded bg-white text-text-primary text-[10px] font-mono border border-border-subtle flex items-center gap-1">
              <IonIcon icon={cashOutline} className="text-emerald-600" />
              <span>In: <strong>${selectedModel.input_cost_per_m}</strong>/1M</span>
            </span>
            <span className="px-2 py-0.5 rounded bg-white text-text-primary text-[10px] font-mono border border-border-subtle flex items-center gap-1">
              <IonIcon icon={cashOutline} className="text-emerald-600" />
              <span>Out: <strong>${selectedModel.output_cost_per_m}</strong>/1M</span>
            </span>
            <span className="px-2 py-0.5 rounded bg-white text-text-primary text-[10px] font-mono border border-border-subtle flex items-center gap-1">
              <IonIcon icon={globeOutline} className="text-brand-purple" />
              <span>Ctx: <strong>{selectedModel.context_window >= 1_000_000 ? `${selectedModel.context_window / 1_000_000}M` : `${selectedModel.context_window / 1000}k`}</strong></span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

