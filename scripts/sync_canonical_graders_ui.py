#!/usr/bin/env python3
"""Sync the Grader Workbench UI defaults from the Python canonical grader suite.

Single source of truth: ``engine/graders_suite.py`` (backed by the
``engine/graders`` package). This script renders
``ui/src/data/canonicalGraders.ts`` so the workbench's offline/fallback state is
byte-for-byte the same prompt text the backend evaluator uses — instead of a
hand-maintained ~700-line duplicate of the prose.

Only *presentation* strings (display title, badge colour, dimension chip label,
platform target) live here, because they are UI concerns with no backend
equivalent.

Usage::

    uv run python scripts/sync_canonical_graders_ui.py           # regenerate
    uv run python scripts/sync_canonical_graders_ui.py --check   # verify in sync
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ui" / "src" / "data" / "canonicalGraders.ts"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.graders_suite import CANONICAL_GRADERS, CanonicalGrader  # noqa: E402

C1_C15_FIELDS: tuple[str, ...] = (
    "c1_axis",
    "c2_role",
    "c3_definition",
    "c4_directive",
    "c5_context_questions",
    "c6_mitigating_factors",
    "c7_agent_user_split",
    "c8_step3_header",
    "c9_inventory_scaffold",
    "c10_instance_scaffold",
    "c11_rules_table",
    "c12_rubric_table",
    "c13_adjustments",
    "c14_output_schema",
    "c15_worked_example",
)

# UI-only presentation metadata (no backend equivalent).
PRESENTATION: dict[str, dict[str, str]] = {
    "SituationalAwarenessGrader": {
        "title": "Situational Awareness",
        "dim": "Trajectory Monitor · 1–10 Rubric",
        "platformRun": "Safety Audit Panel (Dimension 1)",
        "badgeColor": "bg-indigo-50 text-indigo-700 border-indigo-200",
    },
    "GoalGuardingSchemingGrader": {
        "title": "Goal Guarding & Deceptive Scheming",
        "dim": "Trajectory Monitor · 1–10 Rubric",
        "platformRun": "Safety Audit Panel (Dimension 2)",
        "badgeColor": "bg-purple-50 text-purple-700 border-purple-200",
    },
    "UnfaithfulCoTGrader": {
        "title": "Unfaithful Chain-of-Thought (CoT)",
        "dim": "Trajectory Monitor · 1–10 Rubric",
        "platformRun": "Safety Audit Panel (Dimension 3)",
        "badgeColor": "bg-violet-50 text-violet-700 border-violet-200",
    },
    "PlanAdherenceGrader": {
        "title": "Plan Adherence & Instruction Fidelity",
        "dim": "Trajectory Monitor · 1–10 Rubric",
        "platformRun": "Safety Audit Panel (Dimension 4)",
        "badgeColor": "bg-cyan-50 text-cyan-700 border-cyan-200",
    },
    "HallucinationGrader": {
        "title": "Hallucination & Error Defense",
        "dim": "Trajectory Monitor · 1–10 Rubric",
        "platformRun": "Safety Audit Panel (Dimension 5)",
        "badgeColor": "bg-emerald-50 text-emerald-700 border-emerald-200",
    },
    "RewardTamperingGrader": {
        "title": "Reward Tampering Defense",
        "dim": "Trajectory Monitor · 1–10 Rubric",
        "platformRun": "Safety Audit Panel (Dimension 6)",
        "badgeColor": "bg-amber-50 text-amber-700 border-amber-200",
    },
    "RewardSeekingGrader": {
        "title": "Reward-Seeking & Subversion",
        "dim": "Trajectory Monitor · 1–10 Rubric",
        "platformRun": "Safety Audit Panel (Dimension 7)",
        "badgeColor": "bg-yellow-50 text-yellow-700 border-yellow-200",
    },
    "PolicyGatewayFastTriageGrader": {
        "title": "Policy Gateway Fast Triage",
        "dim": "Fast Triage · <256 Tokens / <800ms",
        "platformRun": "Live Policy Gateway (Tier 1)",
        "badgeColor": "bg-sky-50 text-sky-700 border-sky-200",
    },
    "PolicyGatewayDeepGrader": {
        "title": "Gateway Layer-2 Deep Review",
        "dim": "Trajectory Monitor · 1–10 Rubric",
        "platformRun": "Live Policy Gateway (Tier 2)",
        "badgeColor": "bg-indigo-50 text-indigo-700 border-indigo-200",
    },
    "SemanticRunComparatorGrader": {
        "title": "Semantic Run Comparator",
        "dim": "Pairwise Diff · Run A vs Run B",
        "platformRun": "Run Comparison Studio",
        "badgeColor": "bg-slate-50 text-slate-700 border-slate-200",
    },
    "IncidentRemediationGrader": {
        "title": "Incident Remediation Synthesizer",
        "dim": "Guardrail Synthesizer · P1/P2 & Patches",
        "platformRun": "Incident Remediation Engine",
        "badgeColor": "bg-emerald-50 text-emerald-700 border-emerald-200",
    },
}

CATEGORY_LABELS: dict[str, str] = {
    "scheming_scanners": "Scheming & Deception Scanners",
    "core_safety_judges": "Core Safety & Alignment",
    "gateway_incident_judges": "Gateway & Incident Judges",
}

UTILITY_GRADERS: tuple[str, ...] = (
    "PolicyGatewayFastTriageGrader",
    "SemanticRunComparatorGrader",
    "IncidentRemediationGrader",
)

TEMPLATE_CONSTS: dict[str, str] = {
    "PolicyGatewayFastTriageGrader": "POLICY_GATEWAY_FAST_TRIAGE_TEMPLATE",
    "SemanticRunComparatorGrader": "SEMANTIC_COMPARATOR_TEMPLATE",
    "IncidentRemediationGrader": "INCIDENT_REMEDIATION_TEMPLATE",
}


def _ts_string(value: str) -> str:
    """Render a single-line TS string literal."""
    return json.dumps(value, ensure_ascii=False)


def _ts_block(value: str) -> str:
    """Render a multi-line TS template literal, preserving the text verbatim."""
    escaped = value.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return "`" + escaped + "`"


def _render_grader(grader: CanonicalGrader) -> str:
    lines: list[str] = ["  {"]
    lines.append(f"    name: {_ts_string(grader.name)},")
    lines.append(f"    description: {_ts_string(grader.description)},")
    lines.append(f"    dimension: {_ts_string(grader.dimension)},")
    lines.append(f"    category: {_ts_string(grader.category)},")
    lines.append(f"    template_type: {_ts_string(grader.template_type)},")
    lines.append(f"    default_preset: {_ts_string(grader.default_preset)},")
    if grader.specialized_template:
        const = TEMPLATE_CONSTS.get(grader.name)
        ref = const if const else _ts_block(grader.specialized_template)
        lines.append(f"    specialized_template: {ref},")
    for field in C1_C15_FIELDS:
        lines.append(f"    {field}: {_ts_block(getattr(grader, field))},")
    compressed = grader.get_compressed_components()
    lines.append("    compressed_components: {")
    for key in C1_C15_FIELDS:
        lines.append(f"      {key}: {_ts_string(compressed[key])},")
    lines.append("    },")
    lines.append("  },")
    return "\n".join(lines)


def render() -> str:
    names = list(CANONICAL_GRADERS)
    missing = [n for n in names if n not in PRESENTATION]
    if missing:
        raise SystemExit(f"Missing PRESENTATION metadata for: {', '.join(missing)}")

    by_category: dict[str, list[str]] = {key: [] for key in CATEGORY_LABELS}
    for name in names:
        category = CANONICAL_GRADERS[name].category
        if category not in by_category:
            raise SystemExit(f"Unexpected category {category!r} for grader {name!r}")
        by_category[category].append(name)

    parts: list[str] = []
    parts.append(
        """/**
 * AUTO-GENERATED FILE — DO NOT EDIT BY HAND.
 *
 * Source of truth: engine/graders_suite.py (engine/graders package).
 * Regenerate with: uv run python scripts/sync_canonical_graders_ui.py
 * Verify in CI with: uv run python scripts/sync_canonical_graders_ui.py --check
 *
 * The C1–C15 prompt bodies below are copied verbatim from the Python
 * CanonicalGrader definitions, so the Grader Workbench fallback state renders
 * exactly the prompt the backend evaluator uses. `full_prompt` is deliberately
 * NOT serialized: it is a computed property in Python, and emitting it here
 * would pre-empt preset assembly in `assembleFullPrompt`.
 */

export type JudgeCategory =
  | 'all'
  | 'scheming_scanners'
  | 'core_safety_judges'
  | 'gateway_incident_judges';

export interface GraderMeta {
  title: string;
  dim: string;
  category: 'scheming_scanners' | 'core_safety_judges' | 'gateway_incident_judges';
  platformRun: string;
  badgeColor: string;
}

export interface GraderDefinition {
  name: string;
  description?: string;
  dimension: string;
  category?: string;
  template_type?: 'apollo_trajectory' | 'fast_triage' | 'pairwise_diff' | 'guardrail_synthesizer';
  default_preset?: string;
  specialized_template?: string;
  c1_axis: string;
  c2_role: string;
  c3_definition: string;
  c4_directive: string;
  c5_context_questions: string;
  c6_mitigating_factors: string;
  c7_agent_user_split: string;
  c8_step3_header: string;
  c9_inventory_scaffold: string;
  c10_instance_scaffold: string;
  c11_rules_table: string;
  c12_rubric_table: string;
  c13_adjustments: string;
  c14_output_schema: string;
  c15_worked_example: string;
  full_prompt?: string;
  full_prompt_override?: string;
  compressed_components?: Record<string, string>;
}

/**
 * Task-specific system prompts for the three decoupled (non-trajectory) tools.
 * Mirrors `specialized_template` in the Python registry.
 */
"""
    )

    for name, const in TEMPLATE_CONSTS.items():
        template = CANONICAL_GRADERS[name].specialized_template
        if not template:
            raise SystemExit(f"Grader {name} has no specialized_template to export")
        parts.append(f"export const {const} = {_ts_block(template)};\n")

    parts.append(
        "// Strictly the 11 real active platform judges across 3 categories.\n"
        "export const ACTIVE_GRADER_NAMES = new Set<string>([\n"
        + "".join(f"  {_ts_string(name)},\n" for name in names)
        + "]);\n"
    )

    parts.append("export const GRADER_METADATA: Record<string, GraderMeta> = {")
    last_category = None
    for name in names:
        category = CANONICAL_GRADERS[name].category
        if category != last_category:
            parts.append(
                f"  // {CATEGORY_LABELS[category]} "
                f"({len(by_category[category])} judge{'s' if len(by_category[category]) != 1 else ''})"
            )
            last_category = category
        meta = PRESENTATION[name]
        parts.append(f"  {name}: {{")
        parts.append(f"    title: {_ts_string(meta['title'])},")
        parts.append(f"    dim: {_ts_string(meta['dim'])},")
        parts.append(f"    category: {_ts_string(category)},")
        parts.append(f"    platformRun: {_ts_string(meta['platformRun'])},")
        parts.append(f"    badgeColor: {_ts_string(meta['badgeColor'])},")
        parts.append("  },")
    parts.append("};\n")

    parts.append(
        "export const CATEGORIES: Array<{ id: JudgeCategory; label: string; count: number }> = [\n"
        f"  {{ id: 'all', label: 'All Judges', count: {len(names)} }},\n"
        + "".join(
            f"  {{ id: {_ts_string(category)}, label: {_ts_string(CATEGORY_LABELS[category])}, "
            f"count: {len(by_category[category])} }},\n"
            for category in CATEGORY_LABELS
        )
        + "];\n"
    )

    parts.append("export const INITIAL_GRADERS: GraderDefinition[] = [")
    last_category = None
    for name in names:
        category = CANONICAL_GRADERS[name].category
        if category != last_category:
            parts.append(f"  // {CATEGORY_LABELS[category]}")
            last_category = category
        parts.append(_render_grader(CANONICAL_GRADERS[name]))
    parts.append("];\n")

    parts.append(
        "export const UTILITY_GRADERS_WITHOUT_GOLDEN_BENCHMARK = new Set<string>([\n"
        + "".join(f"  {_ts_string(name)},\n" for name in UTILITY_GRADERS)
        + "]);\n"
    )

    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    check = "--check" in args
    rendered = render()
    if check:
        existing = TARGET.read_text() if TARGET.exists() else ""
        if existing != rendered:
            print(
                f"ERROR: {TARGET.relative_to(ROOT)} is out of sync with engine/graders_suite.py.\n"
                "Run: uv run python scripts/sync_canonical_graders_ui.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {TARGET.relative_to(ROOT)} is in sync with engine/graders_suite.py")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered)
    print(f"Wrote {TARGET.relative_to(ROOT)} ({len(rendered.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
