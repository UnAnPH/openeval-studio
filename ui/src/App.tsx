import { useEffect, useRef, useState } from 'react';
import {
  IonApp,
  IonIcon,
  IonPage,
  IonSpinner,
  IonToast,
} from '@ionic/react';
import {
  alertCircleOutline,
  playSharp,
} from 'ionicons/icons';

import { Sidebar } from './components/Sidebar';
import { LiveTrajectory } from './components/LiveTrajectory';
import { InspectViewer } from './components/InspectViewer';
import { Scorecard } from './components/Scorecard';
import { SafetyAuditPanel } from './components/SafetyAuditPanel';
import { DashboardOverview } from './components/DashboardOverview';
import { FirewallGateView } from './components/FirewallGateView';
import { GraderWorkbenchView } from './components/GraderWorkbenchView';
import { SessionsView } from './components/SessionsView';
import { WatcherLiveView } from './components/WatcherLiveView';
import { RunsTable } from './components/RunsTable';
import { RunDetailView } from './components/RunDetailView';
import { BenchmarksList } from './components/BenchmarksList';
import { CompareTestResults } from './components/CompareTestResults';
import { DEFAULT_MODELS, DEFAULT_TASKS } from './data/defaults';
import { DEMO_SESSIONS, DEMO_EVAL_RUNS, DEMO_INTERCEPTIONS } from './data/demoFixtures';
import {
  AgentStep,
  FindingRecord,
  MainNavTab,
  ModelSpec,
  RunRecord,
  TaskSummary,
  WatcherConfig,
  WatcherVerdict,
} from './types';
import { HookSetupView } from './components/HookSetupView';
import { AuthModal } from './components/AuthModal';

const isDemoSurface = () => {
  if (typeof window === 'undefined') return false;
  return window.location.pathname.startsWith('/demo') || window.location.hash.startsWith('#/demo');
};

if (typeof window !== 'undefined' && !(window as any)._openevalFetchInstalled) {
  (window as any)._openevalFetchInstalled = true;
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    if (isDemoSurface()) {
      init = init || {};
      const headers = new Headers(init.headers || {});
      if (!headers.has('X-OpenEval-Surface')) {
        headers.set('X-OpenEval-Surface', 'demo');
      }
      init.headers = headers;
    }
    return originalFetch(input, init);
  };
}

const getInitialUrlRoute = () => {

  try {
    const rawHash = window.location.hash.replace(/^#\/?/, '').trim();
    let basePath = rawHash;
    let queryStr = window.location.search.replace(/^\?/, '');

    if (rawHash.includes('?')) {
      const parts = rawHash.split('?');
      basePath = parts[0];
      queryStr = parts.slice(1).join('?');
    }

    const params = new URLSearchParams(queryStr);
    const task = params.get('task') || params.get('task_id') || undefined;
    const model = params.get('model') || params.get('model_id') || undefined;
    const sessionParam = params.get('session') || params.get('session_id') || params.get('id') || undefined;

    let runId: string | undefined = undefined;
    let incidentId: string | undefined = sessionParam;

    let tab: MainNavTab = 'overview';
    if (basePath === 'studio') tab = 'studio';
    else if (basePath === 'control' || basePath === 'firewall') tab = 'control';
    else if (basePath === 'policy' || basePath === 'watcher_live') tab = 'policy';
    else if (basePath === 'transcripts' || basePath === 'transcript_explorer') tab = 'sessions';
    else if (basePath === 'graders' || basePath === 'grader_workbench') tab = 'graders';
    else if (basePath === 'runs') tab = 'runs';
    else if (basePath.startsWith('runs/')) {
      tab = 'run_detail';
      runId = decodeURIComponent(basePath.replace('runs/', '').trim());
    } else if (basePath.startsWith('sessions/')) {
      tab = 'sessions';
      const sub = decodeURIComponent(basePath.replace('sessions/', '').trim());
      if (sub) incidentId = sub;
    } else if (basePath.startsWith('session/')) {
      tab = 'sessions';
      const sub = decodeURIComponent(basePath.replace('session/', '').trim());
      if (sub) incidentId = sub;
    } else if (basePath.startsWith('incident/')) {
      tab = 'sessions';
      const sub = decodeURIComponent(basePath.replace('incident/', '').trim());
      if (sub) incidentId = sub;
    } else if (basePath === 'session' || basePath === 'sessions') {
      tab = 'sessions';
    } else if (basePath === 'benchmarks' || basePath === 'test_cases') tab = 'benchmarks';
    else if (basePath.startsWith('compare')) tab = 'compare';
    else if (basePath === 'inspect') tab = 'inspect';
    else if (basePath === 'hooks' || basePath === 'hook_setup') tab = 'hooks';

    return { tab, task, model, runId, incidentId };

  } catch {
    return { tab: 'overview' as MainNavTab, task: undefined, model: undefined, runId: undefined, incidentId: undefined };
  }
};

export function App() {
  const initialRoute = getInitialUrlRoute();
  const [navTab, setNavTab] = useState<MainNavTab>(initialRoute.tab);
  const [tasks, setTasks] = useState<TaskSummary[]>(DEFAULT_TASKS);
  const [models, setModels] = useState<ModelSpec[]>(DEFAULT_MODELS);
  const [selectedTaskId, setSelectedTaskId] = useState<string>(initialRoute.task || DEFAULT_TASKS[0]?.task_id || 'cancel-async-tasks');
  const [selectedModelId, setSelectedModelId] = useState<string>(initialRoute.model || DEFAULT_MODELS[0]?.id || 'gemini-3.1-flash-lite');

  // Watcher & Incident State
  const [findings, setFindings] = useState<FindingRecord[]>([]);
  const [discoveredSessions, setDiscoveredSessions] = useState<any[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(initialRoute.incidentId || null);
  const [watcherConfig, setWatcherConfig] = useState<WatcherConfig | undefined>(undefined);
  const [liveInterceptions, setLiveInterceptions] = useState<WatcherVerdict[]>([]);

  const [activeRunId, setActiveRunId] = useState<string | null>(initialRoute.runId || null);
  const [activeRun, setActiveRun] = useState<RunRecord | null>(null);
  const [selectedDetailRunId, setSelectedDetailRunId] = useState<string | null>(initialRoute.runId || null);
  const [comparePair, setComparePair] = useState<[string, string] | null>(null);
  const [liveSteps, setLiveSteps] = useState<AgentStep[]>([]);
  const [runStatus, setRunStatus] = useState<string>('idle');
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [runsHistory, setRunsHistory] = useState<RunRecord[]>([]);
  const [studioInspectorTab, setStudioInspectorTab] = useState<'scorecard' | 'safety' | 'task_info'>('scorecard');
  const [chaosMode, setChaosMode] = useState<boolean>(false);

  const eventSourceRef = useRef<EventSource | null>(null);
  const watcherSseRef = useRef<EventSource | null>(null);
  const evalSettledRef = useRef<boolean>(false);

  const [serverConnected, setServerConnected] = useState<boolean>(false);
  const [isDemoSeed, setIsDemoSeed] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [currentUser, setCurrentUser] = useState<{ user_id: number; username: string } | null>(null);
  const [latestApiKey, setLatestApiKey] = useState<string | null>(null);
  const [showAuthModal, setShowAuthModal] = useState<boolean>(false);
  const isDemo = typeof window !== 'undefined' && isDemoSurface();


  // Persistent client-side tombstone set for deleted runs
  const deletedRunIdsRef = useRef<Set<string>>(new Set());
  // Persistent client-side human audit revisions map
  const runRevisionsRef = useRef<Record<string, RunRecord>>({});

  // Initialize deletedRunIds and runRevisions from localStorage if present
  useEffect(() => {
    try {
      const savedDeleted = localStorage.getItem('openeval_deleted_run_ids');
      if (savedDeleted) {
        const parsed = JSON.parse(savedDeleted);
        if (Array.isArray(parsed)) {
          deletedRunIdsRef.current = new Set(parsed);
        }
      }

      const savedRevisions = localStorage.getItem('openeval_run_revisions');
      if (savedRevisions) {
        runRevisionsRef.current = JSON.parse(savedRevisions) || {};
      }
    } catch (e) {
      console.warn('Could not load local storage states', e);
    }
  }, []);

  // Connect to Watcher Live SSE Telemetry Stream
  useEffect(() => {
    try {
      const sse = new EventSource('/api/watcher/stream');
      watcherSseRef.current = sse;

      sse.addEventListener('connected', (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.config) setWatcherConfig(data.config);
          if (data.history && Array.isArray(data.history)) {
            setLiveInterceptions(data.history);
          }
        } catch {}
      });

      sse.addEventListener('interception_event', (e) => {
        try {
          const verdict: WatcherVerdict = JSON.parse(e.data);
          setLiveInterceptions((prev) => [verdict, ...prev.slice(0, 49)]);
        } catch {}
      });

      sse.addEventListener('interception_resolved', () => {
        fetch('/api/watcher/interceptions')
          .then((r) => r.json())
          .then((rows) => {
            if (Array.isArray(rows)) setLiveInterceptions(rows);
          })
          .catch(() => {});
      });

      sse.addEventListener('tool_result_event', (e) => {
        try {
          const data = JSON.parse(e.data);
          setLiveInterceptions((prev) =>
            prev.map((v) => {
              const matchRev = data.review_id && v.review_id === data.review_id;
              const matchSess = data.session_id && v.session_id === data.session_id;
              if (matchRev || (matchSess && (!v.tool_result || v.tool_result.includes('Awaiting')))) {
                return { ...v, tool_result: data.tool_result };
              }
              return v;
            })
          );
        } catch {}
      });

      sse.addEventListener('config_update', (e) => {
        try {
          const cfg: WatcherConfig = JSON.parse(e.data);
          setWatcherConfig(cfg);
        } catch {}
      });

      return () => {
        sse.close();
      };
    } catch {}
  }, []);

  // Fetch initial data & poll until connected
  useEffect(() => {
    fetchInitialData();
    const interval = setInterval(() => {
      fetchInitialData();
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  const fetchInitialData = async () => {
    try {
      const [healthRes, modelsRes, tasksRes, runsRes, findingsRes, watcherCfgRes, sessionsRes, interceptRes] = await Promise.all([
        fetch('/api/health').catch(() => null),
        fetch('/api/models').catch(() => null),
        fetch('/api/tasks').catch(() => null),
        fetch('/api/eval/runs').catch(() => null),
        fetch('/api/watcher/findings').catch(() => null),
        fetch('/api/watcher/config').catch(() => null),
        fetch('/api/v1/watcher/sessions?min_messages=1').catch(() => null),
        fetch('/api/watcher/interceptions?limit=40').catch(() => null),
      ]);

      let isDemo = isDemoSurface();
      if (healthRes && healthRes.ok) {
        setServerConnected(true);
        setIsDemoSeed(isDemo);
      } else {
        // Appwrite Sites static demo mode (seamless fallback when no backend is running)
        setServerConnected(true);
        setIsDemoSeed(true);
        isDemo = true;
      }

      if (!isDemo) {
        const meRes = await fetch('/api/auth/me').catch(() => null);
        if (meRes && meRes.ok) {
          const user = await meRes.json().catch(() => null);
          if (user) {
            setCurrentUser(user);
            setShowAuthModal(false);
          }
        } else if (meRes && meRes.status === 401) {
          setCurrentUser(null);
          setShowAuthModal(true);
        }
      }


      if (sessionsRes && sessionsRes.ok) {
        const sessData = await sessionsRes.json();
        if (Array.isArray(sessData)) {
          const filtered = isDemo
            ? sessData.filter((s: any) => (s.session_id || s.id || '').includes('demo') || (s.session_id || s.id || '').startsWith('demo-'))
            : sessData;
          setDiscoveredSessions(filtered);
        }
      } else {
        setDiscoveredSessions((prev) => (prev.length === 0 ? (DEMO_SESSIONS as unknown as any[]) : prev));
      }

      if (modelsRes && modelsRes.ok) {
        const data: ModelSpec[] = await modelsRes.json();
        if (data.length > 0) {
          // Reconcile and filter against active catalog to prevent stale backend cache
          const finalModels = DEFAULT_MODELS.map((dm) => ({
            ...dm,
            name: dm.name.replace(/\s*\([^)]*\)/g, '').trim(),
          }));
          setModels(finalModels);
          setSelectedModelId((prev) => (finalModels.some((m) => m.id === prev) ? prev : finalModels[0].id));
        }
      }

      if (tasksRes && tasksRes.ok) {
        const data: TaskSummary[] = await tasksRes.json();
        if (data.length > 0) {
          setTasks(data);
          const currentRawHash = window.location.hash.replace(/^#\/?/, '').trim();
          const currentQuery = currentRawHash.includes('?') ? currentRawHash.split('?')[1] : window.location.search.replace(/^\?/, '');
          const p = new URLSearchParams(currentQuery);
          const target = p.get('task') || p.get('task_id');
          if (target && data.some((t) => t.task_id === target)) {
            setSelectedTaskId(target);
          } else {
            setSelectedTaskId((prev) => (data.some((t) => t.task_id === prev) ? prev : data[0].task_id));
          }
        }
      }

      if (runsRes && runsRes.ok) {
        const data: RunRecord[] = await runsRes.json();
        const activeOnly = data
          .filter((r) => !deletedRunIdsRef.current.has(r.run_id))
          .map((r) => {
            const rev = runRevisionsRef.current[r.run_id];
            return rev ? { ...r, ...rev } : r;
          });

        setRunsHistory(activeOnly);
      } else {
        setRunsHistory((prev) => (prev.length === 0 ? (DEMO_EVAL_RUNS as unknown as RunRecord[]) : prev));
      }

      if (findingsRes && findingsRes.ok) {
        const findingsData: FindingRecord[] = await findingsRes.json();
        if (findingsData.length > 0) {
          setFindings(findingsData);
        }
      } else {
        setFindings((prev) => (prev.length === 0 ? (DEMO_INTERCEPTIONS as unknown as any[]) : prev));
      }

      if (interceptRes && interceptRes.ok) {
        const rows: WatcherVerdict[] = await interceptRes.json();
        if (Array.isArray(rows) && rows.length > 0) {
          setLiveInterceptions((prev) => {
            if (prev.length === 0) return rows;
            const seen = new Set(
              prev.map((v) => `${v.agent_id}|${v.decision}|${v.action_preview}|${v.timestamp}`)
            );
            const merged = [...prev];
            for (const row of rows) {
              const key = `${row.agent_id}|${row.decision}|${row.action_preview}|${row.timestamp}`;
              if (!seen.has(key)) {
                seen.add(key);
                merged.push(row);
              }
            }
            return merged.slice(0, 50);
          });
        }
      } else {
        setLiveInterceptions((prev) => (prev.length === 0 ? (DEMO_INTERCEPTIONS as unknown as any[]) : prev));
      }

      if (watcherCfgRes && watcherCfgRes.ok) {
        const cfgData: WatcherConfig = await watcherCfgRes.json();
        setWatcherConfig(cfgData);
      }
    } catch (err) {
      console.warn('Backend server poll check (running in offline demo mode):', err);
    }
  };

  const handleRotateKey = async (): Promise<string | null> => {
    try {
      const res = await fetch('/api/auth/rotate-key', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setLatestApiKey(data.api_key);
        return data.api_key;
      }
    } catch (e) {
      console.error('Failed to rotate key', e);
    }
    return null;
  };

  const handleLogout = async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch {}
    setCurrentUser(null);
    setLatestApiKey(null);
    if (!isDemo) {
      setShowAuthModal(true);
    }
  };

  const handleLoginSuccess = (user: { user_id: number; username: string }) => {
    setCurrentUser(user);
    setShowAuthModal(false);
    fetchInitialData();
  };

  const handleRegisterSuccess = (user: { user_id: number; username: string; api_key: string }) => {
    setCurrentUser({ user_id: user.user_id, username: user.username });
    setLatestApiKey(user.api_key);
    setShowAuthModal(false);
    setNavTab('hooks');
    setRoute('hooks');
    fetchInitialData();
  };

  // Helper to sync route into URL hash with browser history push
  const setRoute = (hashPath: string, replace: boolean = false) => {

    const clean = hashPath.replace(/^#\/?/, '').trim();
    const formatted = `#${clean}`;
    if (window.location.hash !== formatted) {
      if (replace) {
        window.location.replace(formatted);
      } else {
        window.location.hash = formatted;
      }
    }
  };

  // Two-way synchronization between URL Hash (#...) and UI State + Browser History
  useEffect(() => {
    const syncStateFromHash = async () => {
      const rawHash = window.location.hash.replace(/^#\/?/, '').trim();
      let basePath = rawHash;
      let queryStr = window.location.search.replace(/^\?/, '');

      if (rawHash.includes('?')) {
        const parts = rawHash.split('?');
        basePath = parts[0];
        queryStr = parts.slice(1).join('?');
      }

      const params = new URLSearchParams(queryStr);
      const taskParam = params.get('task') || params.get('task_id');
      if (taskParam) {
        setSelectedTaskId(taskParam);
      }

      const modelParam = params.get('model') || params.get('model_id');
      if (modelParam) {
        setSelectedModelId(modelParam);
      }

      if (!basePath || basePath === 'dashboard' || basePath === 'overview') {
        setNavTab('overview');
        setSelectedDetailRunId(null);
      } else if (basePath === 'control' || basePath === 'firewall') {
        setNavTab('control');
        setSelectedDetailRunId(null);
      } else if (basePath === 'policy' || basePath === 'watcher_live') {
        setNavTab('policy');
        setSelectedDetailRunId(null);
      } else if (basePath === 'hooks' || basePath === 'hook_setup') {
        setNavTab('hooks');
        setSelectedDetailRunId(null);

      } else if (basePath === 'transcripts' || basePath === 'transcript_explorer') {
        setNavTab('sessions');
        setSelectedDetailRunId(null);
      } else if (basePath === 'graders' || basePath === 'grader_workbench') {
        setNavTab('graders');
        setSelectedDetailRunId(null);
      } else if (basePath === 'studio') {
        setNavTab('studio');
      } else if (basePath.startsWith('sessions/')) {
        const sid = decodeURIComponent(basePath.replace('sessions/', '').trim());
        setNavTab('sessions');
        setSelectedDetailRunId(null);
        if (sid) setSelectedSessionId(sid);
      } else if (basePath.startsWith('session/') || basePath.startsWith('incident/')) {
        const sid = decodeURIComponent(basePath.replace(/^(incident|session)\//, '').trim());
        setNavTab('sessions');
        setSelectedDetailRunId(null);
        if (sid) setSelectedSessionId(sid);
      } else if (basePath === 'session' || basePath === 'sessions') {
        setNavTab('sessions');
        setSelectedDetailRunId(null);
        const sessionParam = params.get('session') || params.get('session_id') || params.get('id');
        if (sessionParam) setSelectedSessionId(sessionParam);
      } else if (basePath.startsWith('runs/')) {
        const runId = decodeURIComponent(basePath.replace('runs/', '').trim());
        setNavTab('run_detail');
        setSelectedDetailRunId(runId);
        setActiveRunId(runId);
        try {
          const res = await fetch(`/api/eval/runs/${runId}`).catch(() => null);
          if (res && res.ok) {
            const record: RunRecord = await res.json();
            setActiveRun(record);
            setLiveSteps(record.steps || []);
            setRunStatus(record.status);
          } else {
            const local = runsHistory.find((r) => r.run_id === runId);
            if (local) {
              setActiveRun(local);
              setLiveSteps(local.steps || []);
              setRunStatus(local.status);
            }
          }
        } catch (e) {
          const local = runsHistory.find((r) => r.run_id === runId);
          if (local) {
            setActiveRun(local);
            setLiveSteps(local.steps || []);
            setRunStatus(local.status);
          }
        }
      } else if (basePath === 'runs') {
        setNavTab('runs');
        setSelectedDetailRunId(null);
      } else if (basePath === 'benchmarks' || basePath === 'test_cases') {
        setNavTab('benchmarks');
        setSelectedDetailRunId(null);
      } else if (basePath.startsWith('compare')) {
        setNavTab('compare');
      } else if (basePath === 'inspect') {
        setNavTab('inspect');
      }
    };

    // If no hash at all on initial load, set #dashboard (replace so no duplicate history)
    if (!window.location.hash) {
      window.location.replace('#dashboard');
    } else {
      syncStateFromHash();
    }

    window.addEventListener('hashchange', syncStateFromHash);
    window.addEventListener('popstate', syncStateFromHash);
    return () => {
      window.removeEventListener('hashchange', syncStateFromHash);
      window.removeEventListener('popstate', syncStateFromHash);
    };
  }, [findings, runsHistory]);

  const handleSelectIncident = async (finding: FindingRecord) => {
    const sid = finding.session_id || finding.id;
    setSelectedSessionId(sid);
    setNavTab('sessions');
    setRoute(`session/${encodeURIComponent(sid)}`);
  };

  const handleUpdateWatcherConfig = async (newConfig: Partial<WatcherConfig>) => {
    try {
      const res = await fetch('/api/watcher/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newConfig),
      });
      if (res.ok) {
        const updated: WatcherConfig = await res.json();
        setWatcherConfig(updated);
      }
    } catch (err) {
      console.warn('Failed to update watcher config:', err);
    }
  };

  // Honest failure when offline / API unavailable — never invent a passed run
  const failEvalLaunch = (message: string) => {
    evalSettledRef.current = true;
    setRunStatus('failed');
    setIsStreaming(false);
    setErrorMsg(message);
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  };

  const isTerminalRunStatus = (status: string | undefined | null, run?: RunRecord | null) => {
    if (run && run.passed !== null && run.passed !== undefined) return true;
    const s = String(status || '');
    return [
      'completed',
      'failed',
      'cancelled',
      'error',
      'max_steps_exceeded',
      'timeout',
    ].includes(s);
  };

  const settleEvalRun = (status: string, run?: RunRecord | null) => {
    evalSettledRef.current = true;
    setIsStreaming(false);
    setRunStatus(status);
    if (run) {
      setActiveRun(run);
      if (run.steps?.length) setLiveSteps(run.steps);
    }
  };

  /** REST fallback when SSE drops mid-run — keeps Launch trajectory updating. */
  const pollRunUntilSettled = async (runId: string) => {
    for (let attempt = 0; attempt < 120; attempt++) {
      if (evalSettledRef.current || eventSourceRef.current) {
        return; // settled or a newer stream took over
      }
      try {
        const res = await fetch(`/api/eval/runs/${runId}`);
        if (res.ok) {
          const record: RunRecord = await res.json();
          setActiveRun(record);
          setLiveSteps(record.steps || []);
          setRunStatus(record.status);
          if (isTerminalRunStatus(record.status, record)) {
            settleEvalRun(
              record.status === 'running'
                ? record.passed
                  ? 'completed'
                  : 'error'
                : record.status,
              record
            );
            await fetchInitialData();
            return;
          }
          setIsStreaming(true);
        }
      } catch {
        /* ignore transient poll errors */
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    setIsStreaming(false);
  };

  const handleLaunchEval = async (taskIdToRun?: string) => {
    const targetTaskId = taskIdToRun || selectedTaskId || tasks[0]?.task_id || DEFAULT_TASKS[0].task_id;
    const targetModelId = selectedModelId || models[0]?.id || DEFAULT_MODELS[0].id;

    setSelectedTaskId(targetTaskId);
    evalSettledRef.current = false;
    setIsStreaming(true);
    setRunStatus('running');
    setLiveSteps([]);
    setActiveRun(null);
    setNavTab('studio');

    if (!serverConnected) {
      failEvalLaunch('Backend offline — cannot launch evaluation. Start the API on port 8000.');
      return;
    }

    try {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }

      const launchRes = await fetch('/api/eval/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: targetTaskId,
          model: targetModelId,
          chaos_mode: chaosMode,
        }),
      }).catch(() => null);

      if (!launchRes || !launchRes.ok) {
        let detail = 'Evaluation launch failed.';
        try {
          const errBody = launchRes ? await launchRes.json() : null;
          if (errBody?.detail) {
            detail = typeof errBody.detail === 'string' ? errBody.detail : JSON.stringify(errBody.detail);
          } else if (!launchRes) {
            detail = 'Could not reach /api/eval/run (network error).';
          } else {
            detail = `Evaluation launch failed (HTTP ${launchRes.status}). Check API key / model config.`;
          }
        } catch {
          detail = launchRes
            ? `Evaluation launch failed (HTTP ${launchRes.status}).`
            : 'Could not reach /api/eval/run (network error).';
        }
        failEvalLaunch(detail);
        return;
      }

      const launchData = await launchRes.json();
      const currentRunId = launchData.run_id || `inspect_${Date.now()}`;
      setActiveRunId(currentRunId);

      // Connect to real-time Server-Sent Events stream
      const eventSource = new EventSource(`/api/eval/stream/${currentRunId}`);
      eventSourceRef.current = eventSource;

      eventSource.addEventListener('snapshot', (e) => {
        try {
          const snapshot: RunRecord = JSON.parse(e.data);
          if (snapshot.steps) {
            setLiveSteps(snapshot.steps);
          }
          setActiveRun(snapshot);
        } catch (err) {
          console.warn('Error parsing snapshot event:', err);
        }
      });

      eventSource.addEventListener('step_complete', (e) => {
        try {
          const step: AgentStep = JSON.parse(e.data);
          setLiveSteps((prev) => {
            const exists = prev.some((s) => s.step_number === step.step_number);
            if (exists) {
              return prev.map((s) => (s.step_number === step.step_number ? step : s));
            }
            return [...prev, step];
          });
        } catch (err) {
          console.error('Error parsing step_complete:', err);
        }
      });

      eventSource.addEventListener('completed', async (e) => {
        try {
          const result = JSON.parse(e.data);
          const detailsRes = await fetch(`/api/eval/runs/${currentRunId}`).catch(() => null);
          let fullRecord: RunRecord | null = null;
          if (detailsRes && detailsRes.ok) {
            fullRecord = await detailsRes.json();
            setRunsHistory((prev) => [
              fullRecord!,
              ...prev.filter((r) => r.run_id !== fullRecord!.run_id),
            ]);
          }
          settleEvalRun(result.status || fullRecord?.status || 'completed', fullRecord);
          eventSource.close();
          eventSourceRef.current = null;
          await fetchInitialData();
        } catch (err) {
          console.error('Error parsing completed event:', err);
          settleEvalRun('completed');
          eventSource.close();
          eventSourceRef.current = null;
        }
      });

      eventSource.addEventListener('error', () => {
        // Ignore errors after we've already settled, and ignore transient reconnecting.
        if (evalSettledRef.current || eventSource.readyState === EventSource.CONNECTING) {
          return;
        }
        console.warn('SSE stream closed; falling back to run polling');
        eventSource.close();
        eventSourceRef.current = null;
        void pollRunUntilSettled(currentRunId);
      });
    } catch (err: any) {
      console.warn('Error launching eval:', err);
      failEvalLaunch(err?.message || 'Evaluation launch failed unexpectedly.');
    }
  };

  const handleStopEval = async () => {
    evalSettledRef.current = true;
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setIsStreaming(false);
    setRunStatus('cancelled');

    if (activeRunId) {
      try {
        await fetch(`/api/eval/runs/${activeRunId}/stop`, { method: 'POST' }).catch(() => null);
        await fetchInitialData();
      } catch (err) {
        console.error('Failed to stop run:', err);
      }
    }
  };

  // Human audit save handler
  const handleSaveRevisedAudit = (revisedRun: RunRecord) => {
    runRevisionsRef.current[revisedRun.run_id] = revisedRun;
    try {
      localStorage.setItem('openeval_run_revisions', JSON.stringify(runRevisionsRef.current));
    } catch {}

    setActiveRun(revisedRun);
    setRunsHistory((prev) =>
      prev.map((r) => (r.run_id === revisedRun.run_id ? revisedRun : r))
    );
  };

  // SFT Dataset Export utility
  const handleExportSFTData = (runsToExport: RunRecord[] = runsHistory) => {
    const passingRuns = runsToExport.filter((r) => r.passed === true || r.reward === 1.0);
    if (passingRuns.length === 0) {
      setErrorMsg('No passed evaluation runs found to export for SFT dataset.');
      return;
    }

    const sftData = passingRuns.map((r) => ({
      run_id: r.run_id,
      task_id: r.task_id,
      model: r.model,
      reward: r.reward,
      messages: [
        {
          role: 'system',
          content: 'You are an autonomous software engineering and AI safety agent operating inside an isolated Docker sandbox.',
        },
        {
          role: 'user',
          content: `Solve benchmark task ${r.task_id}: ${r.steps?.[0]?.thought || 'Fix the repository defect.'}`,
        },
        ...(r.steps || []).flatMap((s) => [
          {
            role: 'assistant',
            content: `Thought: ${s.thought}\nAction: ${s.action.tool} ${s.action.command || s.action.path || ''}\n${s.action.content || ''}`,
          },
          {
            role: 'user',
            content: `Observation:\n${s.observation || '(no output)'}`,
          },
        ]),
      ],
    }));

    const jsonlString = sftData.map((d) => JSON.stringify(d)).join('\n');
    const blob = new Blob([jsonlString], { type: 'application/x-jsonlines' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `openeval_sft_dataset_${new Date().toISOString().slice(0, 10)}.jsonl`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // Select a run to view its dedicated full-page RunDetailView
  const handleSelectPastRun = async (runId: string) => {
    setSelectedDetailRunId(runId);
    setActiveRunId(runId);
    setNavTab('run_detail');
    setRoute(`runs/${encodeURIComponent(runId)}`);
    try {
      const res = await fetch(`/api/eval/runs/${runId}`).catch(() => null);
      if (res && res.ok) {
        const record: RunRecord = await res.json();
        setActiveRun(record);
        setLiveSteps(record.steps || []);
        setRunStatus(record.status);
      } else {
        const local = runsHistory.find((r) => r.run_id === runId);
        if (local) {
          setActiveRun(local);
          setLiveSteps(local.steps || []);
          setRunStatus(local.status);
        }
      }
    } catch (err) {
      console.error('Failed to fetch past run:', err);
      const local = runsHistory.find((r) => r.run_id === runId);
      if (local) {
        setActiveRun(local);
        setLiveSteps(local.steps || []);
        setRunStatus(local.status);
      }
    }
  };

  // Delete individual run
  const handleDeleteRun = async (runId: string) => {
    // 1. Record tombstone in memory and localStorage
    deletedRunIdsRef.current.add(runId);
    try {
      localStorage.setItem('openeval_deleted_run_ids', JSON.stringify(Array.from(deletedRunIdsRef.current)));
    } catch (e) {
      console.warn('Failed to persist deleted run ID to localStorage', e);
    }

    // 2. Optimistically update local state immediately
    setRunsHistory((prev) => prev.filter((r) => r.run_id !== runId));
    if (activeRunId === runId) {
      setActiveRunId(null);
      setActiveRun(null);
      setLiveSteps([]);
      setRunStatus('idle');
    }
    if (selectedDetailRunId === runId) {
      setSelectedDetailRunId(null);
      setRoute('runs');
    }

    // 3. Call backend delete (supports both POST and DELETE verbs)
    try {
      await fetch(`/api/eval/runs/${runId}/delete`, { method: 'POST' }).catch(() => null);
      await fetch(`/api/eval/runs/${runId}`, { method: 'DELETE' }).catch(() => null);
    } catch (err) {
      console.warn('Backend delete notification completed with offline state preserved:', err);
    }
  };

  // Clear all historical runs
  const handleClearAllRuns = async () => {
    // 1. Record all current run IDs in tombstones
    runsHistory.forEach((r) => deletedRunIdsRef.current.add(r.run_id));
    try {
      localStorage.setItem('openeval_deleted_run_ids', JSON.stringify(Array.from(deletedRunIdsRef.current)));
    } catch (e) {
      console.warn('Failed to persist deleted run IDs to localStorage', e);
    }

    setRunsHistory([]);
    setActiveRunId(null);
    setActiveRun(null);
    setSelectedDetailRunId(null);
    setLiveSteps([]);
    setRunStatus('idle');

    try {
      await fetch('/api/eval/runs/clear', { method: 'POST' }).catch(() => null);
      await fetch('/api/eval/runs', { method: 'DELETE' }).catch(() => null);
    } catch (err) {
      console.warn('Backend clear all notification completed with offline state preserved:', err);
    }
  };

  // Comparison Handlers
  const handleCompareSelected = (pair: [string, string]) => {
    setComparePair(pair);
    setNavTab('compare');
    setRoute(`compare?a=${encodeURIComponent(pair[0])}&b=${encodeURIComponent(pair[1])}`);
  };

  const handleCompareWithRun = (runId: string) => {
    const otherRun = runsHistory.find((r) => r.run_id !== runId);
    const pair: [string, string] = otherRun ? [runId, otherRun.run_id] : [runId, runId];
    setComparePair(pair);
    setNavTab('compare');
    setRoute(`compare?a=${encodeURIComponent(pair[0])}&b=${encodeURIComponent(pair[1])}`);
  };

  const activeModel = models.find((m) => m.id === selectedModelId);
  const detailRunRecord = runsHistory.find((r) => r.run_id === selectedDetailRunId) || activeRun;
  const detailTask = tasks.find((t) => t.task_id === detailRunRecord?.task_id);

  // Grouped models by provider
  const googleModels = models.filter((m) => m.provider === 'google');
  const localModels = models.filter((m) => m.provider === 'ollama' || m.provider === 'vllm');
  const anthropicModels = models.filter((m) => m.provider === 'anthropic');
  const openaiModels = models.filter((m) => m.provider === 'openai');
  const otherModels = models.filter(
    (m) => !['google', 'ollama', 'vllm', 'anthropic', 'openai'].includes(m.provider)
  );

  return (
    <IonApp className="light">
      <IonPage className="bg-[#F6F5F9] text-text-primary font-sans flex flex-row overflow-hidden">
        {/* Left Navigation Sidebar */}
        <Sidebar
          activeTab={navTab}
          onTabChange={(tab) => {
            if (tab === 'runs') {
              setSelectedDetailRunId(null);
              setRoute('runs');
            } else if (tab === 'overview' || tab === 'dashboard') {
              setRoute('dashboard');
            } else if (tab === 'sessions') {
              setRoute('sessions');
            } else if (tab === 'control' || tab === 'firewall') {
              setRoute('control');
            } else if (tab === 'policy' || tab === 'watcher_live') {
              setRoute('policy');
            } else if (tab === 'hooks') {
              setRoute('hooks');
            } else if (tab === 'transcripts') {
              setRoute('sessions');
            } else if (tab === 'graders') {
              setRoute('graders');
            } else if (tab === 'benchmarks' || tab === 'test_cases') {
              setRoute('benchmarks');
            } else if (tab === 'studio') {
              setRoute('studio');
            } else if (tab === 'compare') {
              setRoute('compare');
            } else if (tab === 'inspect') {
              setRoute('inspect');
            } else {
              setRoute(tab);
            }
          }}
          serverConnected={serverConnected}
          activeModelName={activeModel?.name}
          totalTasks={tasks.length}
          totalRuns={runsHistory.length}
          totalBlocked={findings.length}
          onExportSFT={() => handleExportSFTData()}
          currentUser={currentUser}
          onLogout={handleLogout}
        />

        {/* Main Content Area */}
        <div className="flex-1 flex flex-col min-w-0 h-screen overflow-hidden bg-[#fcfcfd]">
          {isDemo && (
            <div className="bg-gradient-to-r from-purple-900 via-indigo-900 to-purple-950 text-white px-4 py-2 flex items-center justify-between shadow-md border-b border-purple-700/50 text-xs sm:text-sm font-medium z-50 shrink-0">
              <div className="flex items-center gap-2.5">
                <span className="bg-amber-400 text-purple-950 text-[10px] font-extrabold uppercase px-2 py-0.5 rounded tracking-wider shadow-xs">
                  DEMO-ONLY
                </span>
                <span>
                  Demo Mode: Viewing sanitized agent evaluation fixtures. Switch to Live Studio (/)
                </span>
              </div>
              <a
                href="/"
                className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md bg-white/10 hover:bg-white/20 text-white transition-colors border border-white/20 hover:border-white/40 text-xs font-semibold"
              >
                <span>Live Studio (/)</span>
                <span aria-hidden="true">&rarr;</span>
              </a>
            </div>
          )}

          {!serverConnected && (

            <div className="mx-6 mt-3 p-3 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-900 flex items-center justify-between shadow-xs shrink-0">
              <div className="flex items-center gap-2">
                <IonIcon icon={alertCircleOutline} className="text-amber-600 text-base flex-shrink-0" />
                <span>
                  <strong>Backend Server Offline:</strong> Benchmark harness API is unreachable. Start the backend server on port 8000:
                </span>
              </div>
              <code className="px-2 py-0.5 rounded bg-white font-mono text-[11px] border border-amber-200">
                uv run uvicorn server.app:app --port 8000
              </code>
            </div>
          )}

          <div className="flex-1 flex flex-col min-h-0 overflow-hidden">

            {/* TAB 1: OVERVIEW / DASHBOARD (LEAN MISSION CONTROL) */}
            {(navTab === 'overview' || navTab === 'dashboard') && (
              <DashboardOverview
                runs={runsHistory}
                tasks={tasks}
                findings={findings}
                onSelectIncident={handleSelectIncident}
                onNavigateToStudio={() => setRoute('studio')}
                onNavigateToTestCases={() => setRoute('benchmarks')}
                onNavigateToRuns={() => setRoute('runs')}
                onNavigateToFirewall={() => setRoute('control')}
                isDemoSeed={isDemoSeed}
              />
            )}

            {/* SAFETY → Sessions — trajectory browser & transcript search */}
            {(navTab === 'sessions' || navTab === 'incident_detail' || navTab === 'transcripts') && (
              <SessionsView
                initialSessionId={selectedSessionId}
                isDemoSeed={isDemoSeed}
                onBack={() => {
                  if (window.history.length > 1) {
                    window.history.back();
                  } else {
                    setRoute('overview');
                  }
                }}
              />
            )}

            {/* SAFETY → Policy — command rules, tool thresholds, resolve */}
            {(navTab === 'policy' || navTab === 'watcher_live') && (
              <WatcherLiveView
                initialTab="rules"
                onSelectSession={(sessionId) => {
                  setSelectedSessionId(sessionId);
                  setRoute(`sessions/${encodeURIComponent(sessionId)}`);
                }}
              />
            )}

            {/* EVALUATE → Judges */}
            {navTab === 'graders' && <GraderWorkbenchView />}

            {/* SAFETY → Hooks — Setup instructions & key rotation */}
            {navTab === 'hooks' && (
              <HookSetupView
                apiKey={latestApiKey}
                username={currentUser?.username}
                onRotateKey={handleRotateKey}
                isDemo={isDemo}
              />
            )}


            {/* SAFETY → Control — enforce / observe / paused + live feed */}
            {(navTab === 'control' || navTab === 'firewall') && (
              <FirewallGateView
                runs={runsHistory}
                findings={findings}
                discoveredSessions={discoveredSessions}
                watcherConfig={watcherConfig}
                liveInterceptions={liveInterceptions}
                isDemoSeed={isDemoSeed}
                onSelectIncident={handleSelectIncident}
                onUpdateWatcherConfig={handleUpdateWatcherConfig}
                onNavigateToSessions={() => setRoute('sessions')}
              />
            )}

            {/* TAB 2: DEDICATED RUNS REGISTRY */}
            {navTab === 'runs' && (
              <RunsTable
                runs={runsHistory}
                tasks={tasks}
                onSelectRun={handleSelectPastRun}
                onDeleteRun={handleDeleteRun}
                onClearAllRuns={handleClearAllRuns}
                onCompareSelected={handleCompareSelected}
                onExportSFT={handleExportSFTData}
                onNavigateToStudio={() => setRoute('studio')}
                onNavigateToBenchmarks={() => setRoute('benchmarks')}
              />
            )}

            {/* TAB 3: FULL-PAGE RUN DETAIL & MASTER-DETAIL TRAJECTORY */}
            {navTab === 'run_detail' && (
              <RunDetailView
                run={detailRunRecord}
                task={detailTask}
                onBack={() => {
                  if (window.history.length > 1 && window.location.hash.startsWith('#runs/')) {
                    window.history.back();
                  } else {
                    setRoute('runs');
                  }
                }}
                onDeleteRun={handleDeleteRun}
                onSaveRevision={handleSaveRevisedAudit}
                onCompareWith={handleCompareWithRun}
              />
            )}

            {/* TAB 4: BENCHMARKS & TEST SUITES */}
            {(navTab === 'benchmarks' || navTab === 'test_cases') && (
              <BenchmarksList
                tasks={tasks}
                runs={runsHistory}
                onLaunchTask={(taskId) => {
                  setSelectedTaskId(taskId);
                  setRoute(`studio?task=${encodeURIComponent(taskId)}`);
                }}
                onSelectRun={handleSelectPastRun}
                onNavigateToRuns={() => setRoute('runs')}
              />
            )}

            {/* TAB 5: COMPARE RUNS & REGRESSION MATRIX */}
            {navTab === 'compare' && (
              <CompareTestResults
                runs={runsHistory}
                initialRunAId={comparePair?.[0]}
                initialRunBId={comparePair?.[1]}
                onNavigateToRuns={() => setRoute('runs')}
                onNavigateToStudio={() => setRoute('studio')}
              />
            )}

            {/* TAB 6: LIVE STUDIO EVALUATION */}
            {navTab === 'studio' && (
              <div className="flex flex-col h-full bg-[#fcfcfd] text-[#1e2029] font-sans p-6 space-y-6 overflow-y-auto">
                {/* 1. Card Block Header */}
                <div className="bg-white p-4 sm:p-5 rounded-2xl border border-border-subtle shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
                  <div className="flex items-center gap-4 flex-wrap">
                    <h1 className="text-base font-bold text-[#111827] tracking-tight">Launch Evaluation</h1>

                    <div className="h-4 w-px bg-slate-200 hidden sm:block" />

                    {/* Task Dropdown */}
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-slate-500 font-medium">Task:</span>
                      <select
                        value={selectedTaskId}
                        onChange={(e) => setSelectedTaskId(e.target.value)}
                        disabled={isStreaming}
                        className="bg-[#f9fafb] border border-[#e5e7eb] rounded-xl px-3 py-1.5 text-xs text-[#111827] font-medium focus:outline-none focus:border-indigo-500 cursor-pointer max-w-xs"
                      >
                        {tasks.map((t) => (
                          <option key={t.task_id} value={t.task_id}>
                            {t.task_id}
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* Model Dropdown */}
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-slate-500 font-medium">Model:</span>
                      <select
                        value={selectedModelId}
                        onChange={(e) => setSelectedModelId(e.target.value)}
                        disabled={isStreaming}
                        className="bg-[#f9fafb] border border-[#e5e7eb] rounded-xl px-3 py-1.5 text-xs text-[#111827] font-medium focus:outline-none focus:border-indigo-500 cursor-pointer"
                      >
                        {googleModels.length > 0 && (
                          <optgroup label={"Google Gemini" /* & Gemma */}>
                            {googleModels.map((m) => (
                              <option key={m.id} value={m.id} className="bg-white text-text-primary">
                                {m.name}
                              </option>
                            ))}
                          </optgroup>
                        )}

                        {localModels.length > 0 && (
                          <optgroup label="Local & Open-Weight">
                            {localModels.map((m) => (
                              <option key={m.id} value={m.id} className="bg-white text-text-primary">
                                {m.name}
                              </option>
                            ))}
                          </optgroup>
                        )}

                        {anthropicModels.length > 0 && (
                          <optgroup label="Anthropic Claude">
                            {anthropicModels.map((m) => (
                              <option key={m.id} value={m.id} className="bg-white text-text-primary">
                                {m.name}
                              </option>
                            ))}
                          </optgroup>
                        )}

                        {openaiModels.length > 0 && (
                          <optgroup label="OpenAI Frontier">
                            {openaiModels.map((m) => (
                              <option key={m.id} value={m.id} className="bg-white text-text-primary">
                                {m.name}
                              </option>
                            ))}
                          </optgroup>
                        )}

                        {otherModels.length > 0 && (
                          <optgroup label="Custom Models">
                            {otherModels.map((m) => (
                              <option key={m.id} value={m.id} className="bg-white text-text-primary">
                                {m.name}
                              </option>
                            ))}
                          </optgroup>
                        )}
                      </select>
                    </div>
                  </div>

                  <div className="flex items-center gap-2.5">
                    {/* Chaos Mode Switch */}
                    <button
                      type="button"
                      onClick={() => setChaosMode(!chaosMode)}
                      disabled={isStreaming}
                      className={`px-3 py-1.5 rounded-xl text-xs font-mono font-bold transition-all border ${
                        chaosMode
                          ? 'bg-amber-50 text-amber-800 border-amber-300 shadow-xs'
                          : 'bg-[#f3f4f6] text-[#6b7280] border-[#e5e7eb] hover:text-[#111827]'
                      }`}
                    >
                      {chaosMode ? '🐒 Chaos: ON' : '🐒 Chaos: OFF'}
                    </button>

                    {/* Primary Action Button */}
                    {isStreaming ? (
                      <button
                        type="button"
                        onClick={handleStopEval}
                        className="px-4 py-1.5 rounded-xl bg-rose-600 hover:bg-rose-700 text-white font-bold text-xs shadow-xs transition-colors flex items-center gap-1.5 cursor-pointer"
                      >
                        <IonSpinner name="dots" className="w-3 h-3 text-white" />
                        <span>Stop Run</span>
                      </button>
                    ) : (
                      <div className="flex items-center gap-2">
                        {activeRunId && liveSteps.length > 0 && (
                          <button
                            type="button"
                            onClick={() => handleSelectPastRun(activeRunId)}
                            className="px-3 py-1.5 rounded-xl bg-white border border-[#e5e7eb] hover:bg-slate-50 text-indigo-700 font-bold text-xs transition-all cursor-pointer"
                          >
                            <span>Inspect Run &rarr;</span>
                          </button>
                        )}
                        {liveSteps.length > 0 && (
                          <button
                            type="button"
                            onClick={() => {
                              setLiveSteps([]);
                              setActiveRun(null);
                              setActiveRunId(null);
                              setRunStatus('idle');
                            }}
                            className="px-3 py-1.5 rounded-xl bg-white border border-[#e5e7eb] text-slate-600 font-medium text-xs hover:bg-slate-50 transition-all cursor-pointer"
                          >
                            <span>Clear</span>
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => handleLaunchEval()}
                          disabled={!selectedTaskId || !serverConnected}
                          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-[#111827] hover:bg-black text-white text-xs font-bold transition-all shadow-xs disabled:opacity-50 cursor-pointer"
                        >
                          <IonIcon icon={playSharp} className="text-xs" />
                          <span>Launch Evaluation</span>
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                {/* Main Workspace Split (Trajectory on Left, Inspector on Right) */}
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
                  {/* Left Column: Live Trajectory Stream (8 Cols) */}
                  <div className="lg:col-span-8 space-y-4">
                    {/* Visualizer Frame */}
                    <div className="h-[680px]">
                      <LiveTrajectory
                        steps={liveSteps}
                        status={runStatus}
                        isStreaming={isStreaming}
                        run={activeRun}
                      />
                    </div>
                  </div>

                  {/* Right Column: Inspector & Evaluation Results (4 Cols) */}
                  <div className="lg:col-span-4 space-y-4">
                    <div className="bg-white rounded-2xl border border-border-subtle shadow-sm overflow-hidden flex flex-col h-[680px]">
                      {/* Sub-Tabs */}
                      <div className="p-2 bg-canvas/60 border-b border-border-subtle flex items-center justify-between gap-1 shrink-0">
                        <button
                          type="button"
                          onClick={() => setStudioInspectorTab('scorecard')}
                          className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium transition-all text-center ${
                            studioInspectorTab === 'scorecard'
                              ? 'bg-white text-brand-primary font-bold shadow-sm'
                              : 'text-text-secondary hover:text-text-primary'
                          }`}
                        >
                          Scorecard
                        </button>
                        <button
                          type="button"
                          onClick={() => setStudioInspectorTab('safety')}
                          className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium transition-all text-center ${
                            studioInspectorTab === 'safety'
                              ? 'bg-white text-brand-primary font-bold shadow-sm'
                              : 'text-text-secondary hover:text-text-primary'
                          }`}
                        >
                          Safety Audits
                        </button>
                        <button
                          type="button"
                          onClick={() => setStudioInspectorTab('task_info')}
                          className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium transition-all text-center ${
                            studioInspectorTab === 'task_info'
                              ? 'bg-white text-brand-primary font-bold shadow-sm'
                              : 'text-text-secondary hover:text-text-primary'
                          }`}
                        >
                          Task Details
                        </button>
                      </div>

                      {/* Content Area */}
                      <div className="p-4 overflow-y-auto flex-1">
                        {studioInspectorTab === 'scorecard' && (
                          <Scorecard run={activeRun} />
                        )}

                        {studioInspectorTab === 'safety' && (
                          activeRun?.audit_verdicts && activeRun.audit_verdicts.length > 0 ? (
                            <SafetyAuditPanel verdicts={activeRun.audit_verdicts} compact={true} />
                          ) : (
                            <div className="p-8 text-center text-text-muted text-xs space-y-1">
                              <p className="font-bold text-text-primary">No Safety Audits Logged</p>
                              <p className="text-text-secondary">Run an evaluation to inspect automated LLM judge audits.</p>
                            </div>
                          )
                        )}

                        {studioInspectorTab === 'task_info' && (
                          <div className="space-y-3 text-xs">
                            <div className="flex items-center justify-between">
                              <span className="font-mono font-bold text-text-primary">{selectedTaskId}</span>
                              <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-surface-subtle text-brand-purple">
                                {tasks.find((t) => t.task_id === selectedTaskId)?.difficulty || 'Medium'}
                              </span>
                            </div>
                            <div className="space-y-1">
                              <span className="text-[10px] uppercase font-mono text-text-muted font-bold">Instruction:</span>
                              <p className="p-3 bg-canvas rounded-xl border border-border-subtle text-text-secondary leading-relaxed max-h-60 overflow-y-auto">
                                {tasks.find((t) => t.task_id === selectedTaskId)?.instruction_preview || 'No instruction preview available.'}
                              </p>
                            </div>
                            <div className="grid grid-cols-2 gap-2 text-[11px] font-mono text-text-secondary pt-1">
                              <div className="p-2.5 rounded-lg bg-canvas border border-border-subtle">
                                Limit: <strong>{tasks.find((t) => t.task_id === selectedTaskId)?.timeout_sec || 300}s</strong>
                              </div>
                              <div className="p-2.5 rounded-lg bg-canvas border border-border-subtle">
                                Turns: <strong>{tasks.find((t) => t.task_id === selectedTaskId)?.max_steps || 15} max</strong>
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* TAB 8: UK AISI INSPECT PORTAL */}
            {navTab === 'inspect' && (
              <div className="h-[750px]">
                <InspectViewer inspectPort={7575} />
              </div>
            )}
          </div>
        </div>

        {/* Error Toast */}
        <IonToast
          isOpen={!!errorMsg}
          message={errorMsg || ''}
          color="danger"
          duration={5000}
          onDidDismiss={() => setErrorMsg(null)}
          buttons={[{ text: 'Dismiss', role: 'cancel' }]}
        />

        {/* Auth Modal (Sign In / Register) */}
        <AuthModal
          isOpen={showAuthModal && !isDemo}
          onLoginSuccess={handleLoginSuccess}
          onRegisterSuccess={handleRegisterSuccess}
        />
      </IonPage>

    </IonApp>
  );
}

export default App;
