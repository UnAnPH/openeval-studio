import { useEffect, useState } from 'react';
import {
  IonApp,
  IonBadge,
  IonButton,
  IonCard,
  IonContent,
  IonIcon,
  IonLabel,
  IonPage,
  IonSegment,
  IonSegmentButton,
  IonSpinner,
  IonToast,
} from '@ionic/react';
import {
  alertCircleOutline,
  playSharp,
  shieldCheckmarkOutline,
  stopSharp,
  terminalOutline,
} from 'ionicons/icons';

import { Header } from './components/Header';
import { TaskSelector } from './components/TaskSelector';
import { ModelPicker } from './components/ModelPicker';
import { LiveTrajectory } from './components/LiveTrajectory';
import { InspectViewer } from './components/InspectViewer';
import { Scorecard } from './components/Scorecard';
import { SafetyAuditPanel } from './components/SafetyAuditPanel';
import { Leaderboard } from './components/Leaderboard';
import { AgentStep, ModelSpec, RunRecord, TaskSummary } from './types';

export function App() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [models, setModels] = useState<ModelSpec[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>('');
  const [selectedModelId, setSelectedModelId] = useState<string>('gemini-3.1-flash-lite');

  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<RunRecord | null>(null);
  const [liveSteps, setLiveSteps] = useState<AgentStep[]>([]);
  const [runStatus, setRunStatus] = useState<string>('idle');
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [runsHistory, setRunsHistory] = useState<RunRecord[]>([]);
  const [activeTab, setActiveTab] = useState<'live_trajectory' | 'inspect_view'>('live_trajectory');
  const [evalEngine, setEvalEngine] = useState<'openeval' | 'inspect_ai'>('openeval');

  const [serverConnected, setServerConnected] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

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
      const [healthRes, modelsRes, tasksRes, runsRes] = await Promise.all([
        fetch('/api/health').catch(() => null),
        fetch('/api/models').catch(() => null),
        fetch('/api/tasks').catch(() => null),
        fetch('/api/eval/runs').catch(() => null),
      ]);

      if (healthRes && healthRes.ok) {
        setServerConnected(true);
      } else {
        setServerConnected(false);
      }

      if (modelsRes && modelsRes.ok) {
        const data: ModelSpec[] = await modelsRes.json();
        setModels(data);
        if (data.length > 0) {
          setSelectedModelId((prev) => prev || data[0].id);
        }
      }

      if (tasksRes && tasksRes.ok) {
        const data: TaskSummary[] = await tasksRes.json();
        setTasks(data);
        if (data.length > 0) {
          setSelectedTaskId((prev) => prev || data[0].task_id);
        }
      }

      if (runsRes && runsRes.ok) {
        const data: RunRecord[] = await runsRes.json();
        setRunsHistory(data);
      }
    } catch (e) {
      console.error('Failed to fetch initial data:', e);
      setServerConnected(false);
    }
  };

  // Stop Active Evaluation Run
  const handleStopEval = async () => {
    try {
      if (activeRunId) {
        await fetch(`/api/eval/runs/${activeRunId}/stop`, { method: 'POST' });
      } else {
        await fetch('/api/eval/stop_all', { method: 'POST' });
      }
    } catch (err) {
      console.error('Failed to stop run:', err);
    } finally {
      setIsStreaming(false);
      setRunStatus('cancelled');
      fetchInitialData();
    }
  };

  // Launch Evaluation Run
  const handleLaunchEval = async () => {
    if (!selectedTaskId || !selectedModelId) return;

    setErrorMsg(null);
    setIsStreaming(true);

    if (evalEngine === 'inspect_ai') {
      setRunStatus('running (inspect_ai)');
      setActiveTab('inspect_view');
      try {
        const res = await fetch('/api/eval/inspect_run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task_id: selectedTaskId,
            model: selectedModelId,
          }),
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'Failed to launch Inspect AI evaluation');
        }
      } catch (err: any) {
        setErrorMsg(err.message || 'An error occurred launching Inspect AI');
      } finally {
        setIsStreaming(false);
      }
      return;
    }

    setActiveTab('live_trajectory');
    setRunStatus('starting');
    setLiveSteps([]);
    setActiveRun(null);

    try {
      const launchRes = await fetch('/api/eval/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: selectedTaskId,
          model: selectedModelId,
        }),
      });

      if (!launchRes.ok) {
        const err = await launchRes.json();
        throw new Error(err.detail || 'Failed to launch evaluation');
      }

      const launchData = await launchRes.json();
      setActiveRunId(launchData.run_id);
      setRunStatus('running');

      // Subscribe to Server-Sent Events stream
      const eventSource = new EventSource(`/api/eval/stream/${launchData.run_id}`);

      eventSource.addEventListener('start', () => {
        setRunStatus('agent_reasoning');
      });

      eventSource.addEventListener('step_complete', (e) => {
        try {
          const step: AgentStep = JSON.parse(e.data);
          setLiveSteps((prev) => [...prev, step]);
        } catch (err) {
          console.error('Error parsing step_complete:', err);
        }
      });

      eventSource.addEventListener('completed', (e) => {
        try {
          const result = JSON.parse(e.data);
          setRunStatus(result.status || 'completed');
          setIsStreaming(false);
          eventSource.close();

          // Refresh full run & history
          fetchInitialData();
          fetch(`/api/eval/runs/${launchData.run_id}`)
            .then((r) => r.json())
            .then((r) => setActiveRun(r));
        } catch (err) {
          console.error('Error parsing completed event:', err);
        }
      });

      eventSource.addEventListener('error', (e) => {
        console.warn('SSE stream error or end:', e);
        setIsStreaming(false);
        eventSource.close();
        fetchInitialData();
      });
    } catch (err: any) {
      setErrorMsg(err.message || 'An error occurred launching the run');
      setIsStreaming(false);
      setRunStatus('error');
    }
  };

  const handleSelectPastRun = async (runId: string) => {
    setActiveRunId(runId);
    try {
      const res = await fetch(`/api/eval/runs/${runId}`);
      if (res.ok) {
        const record: RunRecord = await res.json();
        setActiveRun(record);
        setLiveSteps(record.steps || []);
        setRunStatus(record.status);
        setIsStreaming(false);
      }
    } catch (err) {
      console.error('Failed to fetch past run:', err);
    }
  };

  const activeModel = models.find((m) => m.id === selectedModelId);

  return (
    <IonApp className="dark">
      <IonPage className="bg-background text-slate-100 font-sans">
        <Header
          serverConnected={serverConnected}
          activeModelName={activeModel?.name}
          totalTasks={tasks.length}
        />

        <IonContent className="ion-padding" scrollY={true}>
          <div className="max-w-7xl w-full mx-auto space-y-6 pb-12">
            {!serverConnected && (
              <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs text-amber-300 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <IonIcon icon={alertCircleOutline} className="text-amber-400 text-base flex-shrink-0" />
                  <span>
                    <strong>Backend Server Offline:</strong> To load benchmark tasks and models, start the API server in your terminal:{' '}
                    <code className="px-2 py-0.5 rounded bg-black/50 font-mono text-amber-200 border border-amber-500/20">
                      uv run uvicorn server.app:app --port 8000
                    </code>
                  </span>
                </div>
              </div>
            )}

            {/* 3-Column Studio Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
              {/* Left Column: Task & Model Config (4 Cols) */}
              <div className="lg:col-span-4 space-y-6">
                <IonCard className="m-0 p-5 rounded-2xl bg-surface border border-border space-y-6 shadow-sm">
                  <ModelPicker
                    models={models}
                    selectedModelId={selectedModelId}
                    onSelectModel={setSelectedModelId}
                    disabled={isStreaming}
                  />

                  <TaskSelector
                    tasks={tasks}
                    selectedTaskId={selectedTaskId}
                    onSelectTask={setSelectedTaskId}
                    disabled={isStreaming}
                  />

                  {/* Evaluation Engine Selector using Ionic Segment */}
                  <div className="space-y-1.5 pt-1">
                    <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center justify-between">
                      <span>Harness Engine</span>
                      <IonBadge color={evalEngine === 'openeval' ? 'primary' : 'tertiary'} className="text-[10px] font-mono font-bold">
                        {evalEngine === 'openeval' ? 'Native ReAct' : 'UK AISI Inspect'}
                      </IonBadge>
                    </label>

                    <IonSegment
                      value={evalEngine}
                      onIonChange={(e) => setEvalEngine(e.detail.value as 'openeval' | 'inspect_ai')}
                      disabled={isStreaming}
                      className="bg-surface-elevated rounded-xl p-1 border border-border"
                    >
                      <IonSegmentButton value="openeval" className="rounded-lg text-xs font-bold">
                        <IonLabel className="text-xs font-bold">⚡ OpenEval ReAct</IonLabel>
                      </IonSegmentButton>
                      <IonSegmentButton value="inspect_ai" className="rounded-lg text-xs font-bold">
                        <IonLabel className="text-xs font-bold">🇬🇧 Inspect AI</IonLabel>
                      </IonSegmentButton>
                    </IonSegment>
                  </div>

                  {/* Launch / Stop CTA */}
                  {isStreaming ? (
                    <div className="space-y-2">
                      <div className="w-full py-2.5 px-4 rounded-xl font-bold text-xs tracking-wide bg-sky-500/10 border border-sky-500/30 text-sky-300 flex items-center justify-center gap-2 glow-active">
                        <IonSpinner name="crescent" color="primary" className="w-4 h-4" />
                        <span>Evaluation In Progress...</span>
                      </div>
                      <IonButton
                        expand="block"
                        color="danger"
                        onClick={handleStopEval}
                        className="font-bold text-xs shadow-lg shadow-rose-600/25"
                      >
                        <IonIcon icon={stopSharp} slot="start" />
                        Stop Active Run
                      </IonButton>
                    </div>
                  ) : (
                    <IonButton
                      expand="block"
                      color="primary"
                      onClick={handleLaunchEval}
                      disabled={!selectedTaskId || !serverConnected}
                      className="font-bold text-sm tracking-wide shadow-lg shadow-sky-500/25"
                    >
                      <IonIcon icon={playSharp} slot="start" />
                      Launch Evaluation Run
                    </IonButton>
                  )}
                </IonCard>

                {/* Scorecard */}
                <Scorecard run={activeRun} />

                {/* AI Safety & Alignment Audits */}
                <SafetyAuditPanel verdicts={activeRun?.audit_verdicts} />
              </div>

              {/* Right Column: Live Trajectory Stream or Inspect View (8 Cols) */}
              <div className="lg:col-span-8 space-y-4">
                {/* View Mode Segment Switcher */}
                <div className="flex items-center justify-between bg-surface p-1.5 rounded-xl border border-border">
                  <IonSegment
                    value={activeTab}
                    onIonChange={(e) => setActiveTab(e.detail.value as 'live_trajectory' | 'inspect_view')}
                    className="max-w-md bg-surface-elevated rounded-lg"
                  >
                    <IonSegmentButton value="live_trajectory" className="rounded-md">
                      <IonIcon icon={terminalOutline} className="mr-1.5" />
                      <IonLabel className="text-xs font-bold">Live Trajectory</IonLabel>
                    </IonSegmentButton>
                    <IonSegmentButton value="inspect_view" className="rounded-md">
                      <IonIcon icon={shieldCheckmarkOutline} className="mr-1.5" />
                      <IonLabel className="text-xs font-bold">UK AISI Inspect</IonLabel>
                    </IonSegmentButton>
                  </IonSegment>

                  <div className="hidden sm:block text-[11px] text-slate-500 font-mono pr-3">
                    {activeTab === 'live_trajectory' ? 'Real-Time SSE Stream' : 'Official Log Visualizer'}
                  </div>
                </div>

                {/* Main Visualizer Frame */}
                <div className="h-[640px]">
                  {activeTab === 'live_trajectory' ? (
                    <LiveTrajectory
                      steps={liveSteps}
                      status={runStatus}
                      isStreaming={isStreaming}
                      run={activeRun}
                    />
                  ) : (
                    <InspectViewer inspectPort={7575} />
                  )}
                </div>

                {/* Leaderboard Table */}
                <Leaderboard
                  runs={runsHistory}
                  onSelectRun={handleSelectPastRun}
                  activeRunId={activeRunId || undefined}
                />
              </div>
            </div>
          </div>
        </IonContent>

        {/* Error Toast */}
        <IonToast
          isOpen={!!errorMsg}
          message={errorMsg || ''}
          color="danger"
          duration={5000}
          onDidDismiss={() => setErrorMsg(null)}
          buttons={[{ text: 'Dismiss', role: 'cancel' }]}
        />
      </IonPage>
    </IonApp>
  );
}

export default App;
