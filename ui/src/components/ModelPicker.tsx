import React from 'react';
import {
  IonBadge,
  IonCard,
  IonCardContent,
  IonChip,
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

  const getTierColor = (tier: string) => {
    switch (tier) {
      case 'flagship':
        return 'warning';
      case 'fast':
        return 'success';
      case 'preview':
        return 'tertiary';
      default:
        return 'primary';
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
          <IonIcon icon={hardwareChipOutline} className="text-sky-400 text-sm" />
          Evaluation Model
        </label>
        {selectedModel && (
          <IonBadge
            color={getTierColor(selectedModel.tier)}
            className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5"
          >
            {selectedModel.tier}
          </IonBadge>
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
          <IonIcon icon={sparklesOutline} className="text-primary text-base" />
        </div>
      </div>

      {selectedModel && (
        <IonCard className="m-0 p-0 rounded-xl bg-surface border border-border/80 text-xs">
          <IonCardContent className="p-3 space-y-2">
            <p className="text-slate-300 leading-relaxed text-xs">{selectedModel.description}</p>
            <div className="flex flex-wrap items-center gap-2 text-slate-400 pt-1 border-t border-border/40">
              <IonChip className="bg-surface-elevated text-slate-300 text-[11px] h-6 px-2">
                <IonIcon icon={cashOutline} color="success" />
                <span className="ml-1">In: <strong>${selectedModel.input_cost_per_m}</strong>/1M</span>
              </IonChip>
              <IonChip className="bg-surface-elevated text-slate-300 text-[11px] h-6 px-2">
                <IonIcon icon={cashOutline} color="success" />
                <span className="ml-1">Out: <strong>${selectedModel.output_cost_per_m}</strong>/1M</span>
              </IonChip>
              <IonChip className="bg-surface-elevated text-slate-300 text-[11px] h-6 px-2">
                <IonIcon icon={globeOutline} color="primary" />
                <span className="ml-1">
                  Ctx: <strong>{selectedModel.context_window >= 1_000_000 ? `${selectedModel.context_window / 1_000_000}M` : `${selectedModel.context_window / 1000}k`}</strong>
                </span>
              </IonChip>
            </div>
          </IonCardContent>
        </IonCard>
      )}
    </div>
  );
};
