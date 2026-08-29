# OpenEval Studio Design System & Interface Specification (`design.md`)

## 1. System Overview & Product Architecture

**OpenEval Studio** is an enterprise-grade AI safety benchmark, autonomous agent evaluation, and alignment audit platform. It combines real-time Docker sandbox execution (SWE-Bench / TerminalBench), UK AISI Inspect AI visualizer integration, and automated LLM-as-a-judge safety verifications into a minimalist, high-contrast light-mode interface.

### 1.1 Core Design Principles
* **Minimalism & Cognitive Focus:** Clutter, duplicate action buttons, and excessive decorative badges are eliminated. The interface prioritizes clean whitespace, clear typography, and a single point of entry for primary actions.
* **Explainable AI & Sandbox Verification:** Every evaluation run provides transparent, turn-by-turn trajectory inspection (Thought → Tool Action → Sandbox Output) alongside held-out pytest verifier assertions and automated LLM judge scoring.
* **High-Contrast Chromatic Hierarchy:** Soft lilac-tinted canvas (`#F6F5F9`) and pure-white cards (`#FFFFFF`) are paired with dark purple-black primary text (`#14121F`) and crisp secondary tones (`#4A4560`) for readability.
* **Predictable 2-Panel Layouts:** Studio and visualizer views separate execution traces from metric scorecards, allowing engineers to track runtime progress without losing contextual results.

---

## 2. Design Tokens & Foundations

### 2.1 Color Palette

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ OpenEval Core Color Palette                                                 │
├─────────────┬─────────────┬─────────────┬─────────────┬─────────────────────┤
│ Pure White  │ Canvas Gray │ Deep Purple │ Dark Base   │ Emerald (Pass)      │
│ #FFFFFF     │ #F6F5F9     │ #2E2682     │ #14121F     │ #10B981             │
└─────────────┴─────────────┴─────────────┴─────────────┴─────────────────────┘
```

#### Primitive & Semantic Color Tokens

| Token Name | Hex Value | Role & Usage |
| :--- | :--- | :--- |
| `--ae-color-surface-white` | `#FFFFFF` | Primary card backgrounds, top navbar, dropdown menus, table rows |
| `--ae-color-canvas-bg` | `#F6F5F9` | Main application workspace canvas, page background |
| `--ae-color-surface-subtle`| `#EBE8F6` | Secondary button backgrounds, filter tag inactive state, hover fills |
| `--ae-color-dark-base` | `#14121F` | Left navigation sidebar, primary action pills, syntax header bars |
| `--ae-color-brand-primary` | `#2E2682` | Primary brand accent, selected navigation states, focus rings |
| `--ae-color-brand-purple` | `#6B46C1` | Accent highlights, brand crest, icon accents |
| `--ae-color-text-primary` | `#14121F` | Primary headings, task IDs, metric numbers |
| `--ae-color-text-secondary` | `#4A4560` | Subtitles, metadata labels, column headers, reasoning copy |
| `--ae-color-text-muted` | `#7E7998` | Placeholders, disabled states, unselected breadcrumb items |
| `--ae-color-border-subtle` | `#E2DFED` | Card borders, table dividers, input borders |
| `--ae-color-border-focus` | `#2E2682` | Active input outline, focused card stroke |

#### Evaluation & Safety Status Tokens

| Status Level | Hex Color | Background Fill | Text / Border | Visual Indicator |
| :--- | :--- | :--- | :--- | :--- |
| **Verification Passed** | `#10B981` | `#ECFDF5` (`bg-emerald-50`) | `#047857` (`text-emerald-700`) | `✓ Passed (1.0)` |
| **Verification Failed** | `#E23636` | `#FFF1F2` (`bg-rose-50`) | `#BE123C` (`text-rose-700`) | `✕ Failed (0.0)` |
| **Evaluation In Progress** | `#6B46C1` | `#F5F3FF` (`bg-purple-50`) | `#6D28D9` (`text-purple-700`) | `● Running...` (Pulse) |
| **Pending / Needs Judgment**| `#F2740E` | `#FFFBEB` (`bg-amber-50`) | `#B45309` (`text-amber-700`) | `• Pending` |

---

## 3. Typography System

The design system pairs **Onest** for structural interface copy with **Space Mono** for code, terminal output, metrics, and case identifiers:

1. **Onest:** Clean, modern neo-grotesque geometric sans-serif (`font-sans`).
2. **Space Mono:** Monospaced typeface for token counts, latencies, reward scores, and command lines (`font-mono`).

### Type Scale Hierarchy

| Level | Font Family | Weight | Size | Usage |
| :--- | :--- | :--- | :--- | :--- |
| **Page Title (H1)** | Onest | Bold (700) | 18px | Top-level view headings |
| **Section Header (H2)**| Onest | Bold (700) | 15px | Card titles, group headers |
| **Body Primary** | Onest | Regular (400) / Medium (500) | 13px | Instructions, reasoning paragraphs |
| **Body Secondary** | Onest | Regular (400) | 12px | Descriptions, helper labels |
| **Micro Caption / Tag**| Space Mono / Onest| Bold (700) | 10px / 11px | Status badges, difficulty pills, overlines |
| **Data Monospace** | Space Mono | Regular (400) / Bold (700) | 11px / 12px | Task IDs, latencies (`14.8s`), spend (`$0.0412`) |

---

## 4. Component & View Architecture

### 4.1 Global Shell & Navigation
* **Sidebar (`Sidebar.tsx`)**:
  * Fixed 56px (224px width) dark base rail (`#14121F`).
  * Minimalist brand crest `⑂ OpenEval`.
  * Clean, spacious navigation items: `Overview`, `Execution Graph`, `Test Cases`, `Live Studio`, `Compare Runs`, `Inspect AI`.
  * Bottom API connectivity status dot (`Connected` / `Offline`).
* **Header (`Header.tsx`)**:
  * Single-line 56px sticky top bar (`#FFFFFF`).
  * Breadcrumb section title (`Evaluations / Overview`).
  * Active model indicator pill (`Gemini 3.1 Flash-Lite`).
  * **Single Primary Point of Entry**: `Run Eval` CTA button.

---

### 4.2 Executive Overview Dashboard (`DashboardOverview.tsx`)
* **4-Card Metric Summary**:
  1. *Accuracy / Pass Rate* (`88%`, `14 of 16 cases verified`).
  2. *Total Evaluations* (`16 runs`, `failed / running counts`).
  3. *Avg Turn Latency* (`14.2s` across autonomous ReAct loops).
  4. *Est. Spend* (`$0.0412`, `84,210 tokens consumed`).
* **Safety & Alignment Verifications**:
  * Clean progress indicators for *Plan Adherence (94%)*, *Hallucination & Error Defense (100%)*, and *Reward Tampering Defense (100%)*.
* **Recent Benchmark Evaluations Table**:
  * Streamlined table with status icons, task IDs, durations, scores, and quick inspection links.

---

### 4.3 Live Studio Workbench (`App.tsx` & `LiveTrajectory.tsx`)
* **Top Unified Control Bar**:
  * Horizontal bar consolidating *Task Dropdown*, *Model Dropdown*, *Harness Engine Toggle (`⚡ OpenEval ReAct` vs `🇬🇧 Inspect AI`)*, and the primary execution CTA (`Launch Evaluation` / `Stop Run`).
* **Main 2-Column Split**:
  * **Left (8 Cols - Trajectory Console)**: Turn-by-turn trace display showing Step numbers, Agent Reasoning & Plan, Bash commands, and Terminal stdout observations.
  * **Right (4 Cols - Tabbed Inspector)**: Clean 3-tab inspector card:
    1. `Scorecard`: Verification result, reward score (1.0/0.0), latency, tokens, spend, and pytest verifier diagnostic output.
    2. `Safety Audits`: LLM-as-a-judge criteria and score breakdown.
    3. `Task Details`: Full task instructions, constraints, and timeout parameters.

---

### 4.4 Spatial Execution Topology (`ExecutionGraph.tsx`)
* **3-Tier Linear Flow**:
  1. *Evaluation Target*: Task specification and model configuration.
  2. *Sandbox Execution & Trace*: Docker container isolation, ReAct trajectory turns, and held-out pytest verifier assertions.
  3. *AI Safety Judges*: Automated plan adherence, hallucination filter, and reward tampering checks.

---

### 4.5 Test Cases Queue & Matrix (`TestCasesTable.tsx`)
* **Filter Pills**: `All`, `Passed`, `Failed`, `Pending`.
* **Clean Data Table**: Status icon, Task ID, Category, Difficulty, Duration, Score, and one-click Inspect / Run actions.

---

### 4.6 Side-by-Side Run Comparison (`CompareTestResults.tsx`)
* **Run A vs Run B Selectors**: Easy selection of current run vs baseline.
* **2-Column Diff Matrix**: Side-by-side metric comparison, prompt input, agent resolution, and tool execution profiles.

---

## 5. Technology Stack & Rules

* **Framework**: React 18+ with TypeScript.
* **Component UI**: Ionic React (`@ionic/react`) + Ionicons (`ionicons`).
* **Styling**: Tailwind CSS 3.x with native `@layer` declarations in `src/index.css`.
* **Theme Enforcement**:
  * Pure light mode.
  * Root CSS variables (`--ion-background-color: #F6F5F9`, `--ion-text-color: #14121F`, `--ion-card-background: #FFFFFF`) bound directly to `:root, :root.ios, :root.md, html, body`.
  * `@ionic/react/css/palettes/dark.always.css` is strictly forbidden.
