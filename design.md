# OpenEval Studio Unified Design System & Interface Specification (`design.md`) 🎨

> **The definitive design system and interface specification for OpenEval Studio. All autonomous agents and engineers contributing to this repository must follow these design tokens, component patterns, and layout structures to maintain visual consistency.**

---

## 1. Design Philosophy & High-Contrast Light Mode

OpenEval Studio implements a **high-contrast, minimalist light-mode design system** optimized for complex autonomous trajectory inspection, safety auditing, and real-time monitoring.

### 1.1 Core Principles
1. **Single Unified Visual Identity:** Regardless of which model or engineer generated a view, all 4 trajectory views (Sessions, Watcher Live, Live Autonomous Trajectory Trace, and Finished Run Trajectory Trace) share identical card containers, headers, typography, and color tokens.
2. **Cognitive Clarity:** High-density data (tokens, latencies, exit codes, diffs) is presented with strict visual hierarchy. Monospaced metadata is cleanly separated from human-readable agent reasoning.
3. **Zero Ionic Dependencies:** 100% of icons and UI elements utilize native `lucide-react` SVG components and Tailwind CSS. `@ionic/react` and `ionicons` are strictly prohibited in core views to eliminate hydration and layout squishing bugs.
4. **Copyable & Terminal-Centric:** All bash commands, observations, and tool payloads render inside dark monospace code blocks with one-click copy buttons and standard command prompts (`$ `).

---

## 2. Color Palette & Semantic Tokens

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ OpenEval Studio Core Palette                                                │
├─────────────┬─────────────┬─────────────┬─────────────┬─────────────────────┤
│ Pure White  │ Canvas Gray │ Slate Base  │ Dark Code   │ Indigo Brand        │
│ #FFFFFF     │ #F8FAFC     │ #0F172A     │ #020617     │ #4F46E5             │
└─────────────┴─────────────┴─────────────┴─────────────┴─────────────────────┘
```

### 2.1 Primitive Surface & Text Tokens

| Token | Class Name | Hex Value | Usage |
| :--- | :--- | :--- | :--- |
| **Card Surface** | `bg-white` | `#FFFFFF` | Primary card panels, modal dialogues, flyout menus |
| **Canvas Background** | `bg-slate-50` / `bg-[#fcfcfd]` | `#F8FAFC` | Workspace canvas, app layout container background |
| **Subtle Surface** | `bg-slate-100` / `bg-slate-50/70` | `#F1F5F9` | Tab rails, secondary buttons, unselected item fills |
| **Dark Code Surface** | `bg-slate-950` | `#020617` | Code blocks, stdout terminals, execution logs |
| **Primary Text** | `text-slate-900` | `#0F172A` | Headings, primary labels, task names, metric numbers |
| **Secondary Text** | `text-slate-600` | `#475569` | Body copy, agent thoughts, explanations, table text |
| **Muted Text** | `text-slate-400` / `text-slate-500` | `#94A3B8` | Metadata, turn timestamps, token counts, placeholders |
| **Subtle Border** | `border-border-subtle` / `border-slate-200` | `#E2E8F0` | Card borders, dividers, tab rails, table rows |
| **Dark Code Border** | `border-slate-800` | `#1E293B` | Stroke around dark terminal and code pre-blocks |

### 2.2 Safety Verdict & Status Tokens

| Status | Background | Text Color | Border Color | Visual Indicator |
| :--- | :--- | :--- | :--- | :--- |
| **Passed / Cleared** | `bg-emerald-50` | `text-emerald-700` | `border-emerald-200` | `CheckCircle2` (Emerald) |
| **Blocked / Critical** | `bg-rose-50` | `text-rose-700` | `border-rose-200` | `XCircle` / `ShieldAlert` (Rose) |
| **Warning / Escalate** | `bg-amber-50` | `text-amber-700` | `border-amber-200` | `AlertTriangle` (Amber) |
| **Active / Executing** | `bg-indigo-50` | `text-indigo-700` | `border-indigo-200` | `Loader2` (Spinning Indigo) |
| **Neutral / Parked** | `bg-slate-100` | `text-slate-700` | `border-slate-200` | `Clock` / `Minus` (Slate) |

---

## 3. Typography Hierarchy

OpenEval Studio uses a dual-font strategy:
1. **Primary Structural Sans (`font-sans`):** Clean, modern geometric sans-serif (`Inter`, system fallback) for headings, body text, buttons, and reasoning narratives.
2. **Technical Data Monospace (`font-mono`):** High-legibility code font (`JetBrains Mono`, `Space Mono`) for step numbers, token counts, execution latencies, costs, bash commands, JSON payloads, and diffs.

### 3.1 Type Scale Specification

| Role | Family | Weight | Size | Line Height | Tailwind Classes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Page Title** | Sans | Bold (700) | 16px–18px | 1.25 | `text-base sm:text-lg font-bold text-slate-900 tracking-tight` |
| **Card Header** | Mono | Bold (700) | 14px | 1.25 | `text-sm font-bold text-slate-900 font-mono tracking-tight` |
| **Section Label** | Mono | Bold (700) | 10px | 1.0 | `text-[10px] font-mono uppercase font-bold tracking-wider text-slate-500` |
| **Body Primary** | Sans | Regular (400) | 12px–13px | 1.5 | `text-xs sm:text-sm text-slate-700 leading-relaxed font-sans` |
| **Code / Terminal**| Mono | Regular (400) | 11px–12px | 1.5 | `text-xs font-mono text-slate-100 leading-relaxed select-text` |
| **Metadata Tag** | Mono | Medium (500) | 10px–11px | 1.0 | `text-[10px] sm:text-[11px] font-mono text-slate-500` |

---

## 4. Canonical Component Patterns

### 4.1 Card Container
Every main container, pane, and modal panel must use the standardized card container:
```tsx
<div className="bg-white rounded-2xl border border-border-subtle shadow-sm overflow-hidden flex flex-col">
  {/* Content */}
</div>
```

### 4.2 Top Flight Header Bar
Header bar atop studio views with breadcrumb, live status, and actions:
```tsx
<div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex items-center justify-between shrink-0">
  <div className="flex items-center gap-3">
    <div className="w-8 h-8 rounded-xl bg-slate-100 text-slate-700 flex items-center justify-center border border-slate-200">
      <Terminal className="w-4 h-4" />
    </div>
    <div>
      <h1 className="text-sm sm:text-base font-bold text-slate-900 tracking-tight font-mono">
        View Title
      </h1>
      <span className="text-[11px] text-slate-500 font-mono">
        Subtext or metadata counter
      </span>
    </div>
  </div>
  <div className="flex items-center gap-2">
    {/* Status pill or CTA */}
  </div>
</div>
```

### 4.3 Pill Tab Navigation
Unified tab switcher used across Run Detail, Watcher Live, and Grader Workbench:
```tsx
<div className="bg-white p-1.5 rounded-2xl border border-border-subtle shadow-sm flex items-center gap-1.5 text-xs font-semibold overflow-x-auto shrink-0">
  {tabs.map((tab) => {
    const isActive = activeTab === tab.id;
    return (
      <button
        key={tab.id}
        type="button"
        onClick={() => setActiveTab(tab.id)}
        className={`px-3.5 py-2 rounded-xl flex items-center gap-2 whitespace-nowrap transition-colors cursor-pointer shrink-0 ${
          isActive
            ? 'bg-slate-900 text-white shadow-xs font-semibold'
            : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50 font-medium'
        }`}
      >
        <tab.icon className={`w-3.5 h-3.5 shrink-0 ${isActive ? 'text-white' : 'text-slate-500'}`} />
        <span>{tab.label}</span>
      </button>
    );
  })}
</div>
```

### 4.4 Dark Monospace Code / Terminal Block
Used for all bash command executions, file mutations, and container observation logs:
```tsx
<div className="space-y-1">
  <div className="flex items-center justify-between text-[10px] uppercase font-mono tracking-wider text-slate-500 font-bold">
    <div className="flex items-center gap-1 text-amber-700">
      <Terminal className="w-3 h-3" />
      <span>Executed Command</span>
    </div>
    <button
      type="button"
      onClick={() => handleCopy(commandText, 'cmd-id')}
      className="text-slate-400 hover:text-slate-700 flex items-center gap-1 transition-colors cursor-pointer"
    >
      {copied ? (
        <>
          <Check className="w-3 h-3 text-emerald-600" />
          <span className="text-emerald-600">Copied</span>
        </>
      ) : (
        <>
          <Copy className="w-3 h-3" />
          <span>Copy</span>
        </>
      )}
    </button>
  </div>
  <pre className="bg-slate-950 text-slate-100 font-mono text-xs p-3.5 rounded-xl border border-slate-800 select-text overflow-x-auto">
    <span className="text-emerald-400 select-none font-bold">$ </span>
    {commandText}
  </pre>
</div>
```

### 4.5 Watcher Runtime Interception Crimson Banner
Displayed whenever the 4-stage safety firewall intercepts an unsafe tool invocation:
```tsx
<div className="p-4 rounded-xl bg-rose-50 border border-rose-200 text-xs text-rose-950 space-y-3">
  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-rose-200 pb-2">
    <div className="flex items-center gap-2">
      <div className="w-6 h-6 rounded-lg bg-rose-600 text-white flex items-center justify-center shadow-xs">
        <ShieldAlert className="w-4 h-4 text-white" />
      </div>
      <div className="font-bold font-mono text-rose-900 text-xs flex items-center gap-1.5">
        <span>WATCHER LIVE: RUNTIME INTERCEPTION</span>
        <span className="px-2 py-0.5 rounded bg-rose-100 text-rose-800 text-[10px] uppercase font-bold">
          {ruleTag}
        </span>
      </div>
    </div>
    <div className="flex items-center gap-2 bg-white px-2.5 py-1 rounded-lg border border-rose-200 font-mono text-[11px] shadow-xs">
      <span className="text-slate-500">Risk Score:</span>
      <strong className="text-rose-700 font-bold">{riskScore.toFixed(2)} / 1.00</strong>
    </div>
  </div>

  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
    <div className="p-3 bg-white rounded-xl border border-rose-200 space-y-1.5 shadow-xs">
      <div className="text-[10px] font-mono font-bold uppercase text-rose-800">
        ⛔ Proposed Action (Blocked Before Execution)
      </div>
      <pre className="font-mono text-xs text-rose-700 bg-rose-50 p-2.5 rounded-lg overflow-x-auto whitespace-pre-wrap border border-rose-200">
        {blockedCommand}
      </pre>
    </div>
    <div className="p-3 bg-white rounded-xl border border-rose-200 space-y-1.5 shadow-xs">
      <div className="text-[10px] font-mono font-bold uppercase text-emerald-800">
        🛡️ Enforced Observation (Injected into Context)
      </div>
      <div className="font-mono text-xs text-slate-800 leading-relaxed bg-slate-50 p-2.5 rounded-lg whitespace-pre-wrap border border-slate-200">
        {enforcedObservation}
      </div>
    </div>
  </div>
</div>
```

---

## 5. View Specifications & Harmonization Rules

### 5.1 Sessions & Transcript Explorer (`SessionsView.tsx` & `TranscriptCardView.tsx`)
* **Status Filter Toolbar:** 4-tab segmented control (`grid grid-cols-4`):
  * **All:** Total ingested session count.
  * **Live:** Actively executing sessions (`working`, `active`, `running`) featuring a pulsing emerald indicator (`w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse`).
  * **Blocked:** Sessions containing at least one policy violation or blocked turn (accented with rose badges when count $>0$).
  * **Closed:** Finished sessions (renamed consistently from `Completed`).
* **Noise Suppression:** Zero-message ghost sessions are filtered out from the sidebar.
* **Left Master List:** 340px fixed width, search bar with `Search` icon, list items with active left indigo border indicator (`border-l-4 border-l-indigo-600`), agent source pill, duration, and turn count.
* **Right Transcript Detail:**
  * Header with session title, session ID copy badge, model, duration, and token counter.
  * Search chips: Hybrid search results showing Dense Vector ($60\%$), BM25 ($40\%$), and match percentage badges.
  * Turn cards with expandable agent thought, full unclipped tool parameters, and observation stdout blocks.

### 5.2 Safety Control & Firewall Telemetry (`FirewallGateView.tsx`)
* **Mode Header:** High-contrast pill toggle for runtime mode (`Enforce` [destructive lockout] / `Observe` [shadow log] / `Paused` [allow all]).
* **Stream Filter Toolbar:** 3-tab segmented control (`grid grid-cols-3`):
  * **All:** Total intercepted live events.
  * **Blocked:** Active security policy violations (accented rose when count $>0$).
  * **Closed:** Resolved, permitted, or operator-approved tool calls.
  * *(Design Contract: The `Live` status filter is exclusively reserved for `SessionsView` to prevent redundant telemetry tabs).*
* **Streamlined Telemetry Rows:** De-cluttered row format displaying expand chevron, timestamp, decision badge (`🛑 Denied` / `✓ Allowed` / `👤 Allowed (Human)`), agent source (`🤖 Antigravity`, `🖱️ Cursor`, `⚡ Claude Code`), and monospace action preview.
* **Dual-Pane Expandable Detail Tray:**
  * **Full Command:** Dark code surface (`bg-[#14121F]`) displaying unclipped command string with single-click `Copy Command` button.
  * **Execution Output:** Terminal surface (`bg-[#0b1329] text-emerald-300`) displaying complete command stdout/stderr or gate lockout message with `Copy Output` button.
  * **Metadata Grid:** Rounded latency (`Math.round(v.latency_ms) ms`), verification stage, and policy note.
  * **Operator Resolution:** `👤 Mark as Operator Allowed` on blocked events, plus a top toolbar action `👤 Allow All for Clarity` when pending blocks exist.

### 5.3 Live Autonomous Trajectory Trace (`LiveTrajectory.tsx`)
* **Phase Stepper:** 4-step linear flow (`1. Sandbox Init` $\rightarrow$ `2. Autonomous ReAct` $\rightarrow$ `3. Pytest Verifier` $\rightarrow$ `4. LLM Judge Audits`) with pulsating indicators.
* **Streaming Badge:** Animated `Loader2` spinner with `Agent Executing` label.
* **Turn Cards:** Sequential `#N` badges, latency, tokens, agent thought callout, and terminal output blocks.

### 5.4 Run Detail Inspector (`RunDetailView.tsx`)
* **KPI Ribbon:** Durations, total tokens, estimated cost, passed/failed verdict.
* **Master-Detail Two-Pane Grid:** Left 4-column step list with turn selection; right 8-column deep inspector showing thought, command, stdout, and verifier diagnostics.
* **Tab Panels:** Trajectory, Held-Out Verifier Output, Code Mutations, and Audit Report Sign-Off.

---

## 6. Iconography Rules for Collaborating Agents

All icons must be imported exclusively from `lucide-react`:

```tsx
// ✅ Correct
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Clock,
  Code2,
  Copy,
  DollarSign,
  Download,
  FileText,
  GitCompare,
  Hourglass,
  Layers,
  Loader2,
  Maximize2,
  Minimize2,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Terminal,
  Trash2,
  XCircle,
} from 'lucide-react';

// ❌ FORBIDDEN: Do not import @ionic/react or ionicons
import { IonIcon, IonSpinner } from '@ionic/react';
import { checkmarkCircle, terminalOutline } from 'ionicons/icons';
```

---

## 7. Quality Checklist for New UI Changes

When creating or refactoring a UI component, verify:
1. Card container uses `bg-white rounded-2xl border border-border-subtle shadow-sm`.
2. All buttons use cursor pointer and standard pill/rounded styles (`cursor-pointer`).
3. No Ionic imports exist in the modified file.
4. All code blocks use `bg-slate-950 text-slate-100 font-mono text-xs p-3.5 rounded-xl border border-slate-800`.
5. TypeScript build succeeds cleanly: `cd ui && npm run build` (0 errors).
