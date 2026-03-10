import { useState, useEffect, useCallback, useRef } from 'react';
import { AlertTriangle, Clock, Activity, CheckCircle2, Map as MapIcon, ShieldAlert, Pencil } from 'lucide-react';
import { MapContainer, TileLayer, Circle, Popup, Marker } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import classNames from 'classnames';

// --- Types ---
type CrisisPhase = 'RETRIEVAL' | 'PLANNING' | 'HITL_REVIEW' | 'EXECUTION' | 'COMPLETED';

interface ActiveCrisis {
  id: string;
  title: string;
  disaster_type: string;
  severity: number;
  phase: CrisisPhase;
  warning_lead_time_h: number;
  latitude: number;
  longitude: number;
  affected_radius_m: number;
  status: string;
  orchestration_state?: any;
}

interface SmsReport {
  id: string;
  phone: string;
  text: string;
  is_spam: boolean;
  timestamp: string;
  // simplified location
  lat?: number;
  lon?: number;
}

export default function App() {
  const [crises, setCrises] = useState<ActiveCrisis[]>([]);
  const [selectedCrisisId, setSelectedCrisisId] = useState<string | null>(null);
  const [recentReports, setRecentReports] = useState<SmsReport[]>([]);
  const [wsConnected, setWsConnected] = useState(false);
  const [editingTasks, setEditingTasks] = useState<Record<string, string>>({}); // taskId -> edited action text
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // --- Initial Data Fetch ---
  useEffect(() => {
    fetch('http://localhost:8000/crises')
      .then(res => res.json())
      .then(data => setCrises(data))
      .catch(err => console.error("Failed to load crises on mount:", err));
  }, []);

  // --- WebSocket Connection with Auto-Reconnect ---
  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket('ws://localhost:8000/ws');
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('✅ Connected to ADRC Backend');
      setWsConnected(true);
    };

    ws.onclose = () => {
      console.log('❌ Disconnected — reconnecting in 3s...');
      setWsConnected(false);
      wsRef.current = null;
      reconnectTimer.current = setTimeout(connectWebSocket, 3000);
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        console.log("WebSocket event:", message);

        if (message.type === 'NEW_SMS_REPORT') {
          setRecentReports(prev => [message.data, ...prev].slice(0, 10));
        } 
        else if (message.type === 'CRISIS_CONFIRMED') {
          setCrises(prev => {
            const exists = prev.find(c => c.id === message.data.id);
            if (exists) {
              return prev.map(c => c.id === message.data.id ? { ...c, ...message.data } : c);
            }
            return [message.data, ...prev];
          });
          setSelectedCrisisId(prev => prev ? prev : message.data.id);
        }
        else if (message.type === 'ORCHESTRATION_UPDATED') {
          setCrises(prev => prev.map(c => {
            if (c.id === message.data.crisis_id) {
              return {
                ...c,
                phase: message.data.phase,
                orchestration_state: {
                  ...c.orchestration_state,
                  plan: message.data.plan,
                  phase: message.data.phase
                }
              };
            }
            return c;
          }));
        }
        else if (message.type === 'TASK_STATUS_UPDATED') {
          // Show responder reply in the live feed
          setRecentReports(prev => [{
            id: message.data.assignment_id,
            phone: message.data.node_name,
            text: `Task ${message.data.new_status} ✅`,
            is_spam: false,
            timestamp: message.data.responded_at,
          }, ...prev].slice(0, 10));
        }
      } catch (err) {
        console.error("Error parsing WS message:", err);
      }
    };
  }, []);

  useEffect(() => {
    connectWebSocket();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connectWebSocket]);

  // --- Action Handlers ---
  const triggerOrchestration = async (crisisId: string) => {
    try {
      await fetch(`http://localhost:8000/crises/${crisisId}/orchestrate`, { method: 'POST' });
    } catch (err) {
      console.error("Trigger failed:", err);
    }
  };

  const approvePlan = async (crisisId: string) => {
    try {
      // Send any edited tasks along with the approval
      const payload: any = { comment: "Approved via dashboard" };
      if (Object.keys(editingTasks).length > 0 && selectedCrisis?.orchestration_state?.plan?.tasks) {
        payload.tasks = selectedCrisis.orchestration_state.plan.tasks.map((task: any) => ({
          ...task,
          action: editingTasks[task.id] ?? task.action,
        }));
      }
      await fetch(`http://localhost:8000/crises/${crisisId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      setEditingTasks({}); // Clear edits after approval
    } catch (err) {
      console.error("Approve failed:", err);
    }
  };


  const selectedCrisis = crises.find(c => c.id === selectedCrisisId);

  return (
    <div className="flex h-screen w-screen bg-slate-950 text-slate-200 overflow-hidden font-sans">
      
      {/* LEFT SIDEBAR: Crises List */}
      <div className="w-96 border-r border-slate-800 flex flex-col bg-slate-900">
        <div className="p-4 border-b border-slate-800 flex justify-between items-center">
          <div>
            <h1 className="text-xl font-bold text-white flex gap-2 items-center">
              <ShieldAlert className="text-blue-500" /> ADRC Control
            </h1>
            <p className="text-xs text-slate-400 mt-1">Multi-Agent Orchestrator</p>
          </div>
          <div title={wsConnected ? 'Connected to live feed' : 'Disconnected'} 
               className={classNames("w-3 h-3 rounded-full animate-pulse", wsConnected ? "bg-green-500" : "bg-red-500")} />
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <h2 className="text-xs uppercase font-semibold tracking-wider text-slate-500 mb-2">Active Crises</h2>
          
          {crises.length === 0 && (
            <div className="text-center p-8 text-slate-500 text-sm border border-dashed border-slate-700 rounded-lg">
              No active crises.<br/>Awaiting SMS clusters.
            </div>
          )}

          {crises.map(crisis => (
            <div 
              key={crisis.id}
              onClick={() => setSelectedCrisisId(crisis.id)}
              className={classNames(
                "p-3 rounded-xl border cursor-pointer transition-all",
                selectedCrisisId === crisis.id 
                  ? "bg-slate-800 border-blue-500/50 shadow-[0_0_15px_rgba(59,130,246,0.1)]" 
                  : "bg-slate-800/50 border-slate-700 hover:border-slate-600"
              )}
            >
              <div className="flex justify-between items-start mb-2">
                <span className="font-semibold text-white truncate pr-2">{crisis.title}</span>
                <span className={classNames(
                  "text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap",
                  crisis.severity >= 4 ? "bg-red-500/20 text-red-400 border border-red-500/20" : "bg-orange-500/20 text-orange-400 border border-orange-500/20"
                )}>
                  Lvl {crisis.severity}
                </span>
              </div>
              <div className="flex items-center gap-2 text-xs text-slate-400 mb-3">
                <Activity size={14} />
                <span>{crisis.disaster_type}</span>
                <span className="text-slate-600">•</span>
                <span>{crisis.warning_lead_time_h === 0 ? 'Sudden Onset' : `${crisis.warning_lead_time_h}h Lead`}</span>
              </div>
              
              {/* Status Pill */}
              <div className={classNames(
                "text-xs px-2 py-1.5 rounded bg-slate-900 border flex items-center gap-2",
                crisis.phase === 'HITL_REVIEW' ? "border-yellow-500/50 text-yellow-500" :
                crisis.phase === 'EXECUTION' ? "border-green-500/50 text-green-500" :
                "border-blue-500/30 text-blue-400"
              )}>
                {crisis.phase === 'HITL_REVIEW' ? <AlertTriangle size={14}/> : 
                 crisis.phase === 'EXECUTION' ? <CheckCircle2 size={14}/> : 
                 <Clock size={14}/>}
                <span className="font-semibold tracking-wide">
                  {crisis.phase || 'PENDING'}
                </span>
              </div>
            </div>
          ))}

          <h2 className="text-xs uppercase font-semibold tracking-wider text-slate-500 mt-8 mb-2">Live SMS Feed</h2>
          <div className="space-y-2">
            {recentReports.map(r => (
              <div key={r.id} className="p-2 text-xs bg-slate-900 rounded border border-slate-800">
                <div className="flex justify-between text-slate-500 mb-1">
                  <span>{r.phone}</span>
                  <span>Just now</span>
                </div>
                <div className="text-slate-300">"{r.text}"</div>
              </div>
            ))}
          </div>

        </div>
      </div>

      {/* RIGHT MAIN PANEL */}
      <div className="flex-1 flex flex-col relative bg-[#1e293b]">
        {!selectedCrisis ? (
          <div className="flex-1 flex items-center justify-center text-slate-500">
            <div className="text-center">
              <MapIcon className="w-16 h-16 mx-auto mb-4 opacity-20" />
              <p>Select a crisis from the sidebar to view the dashboard.</p>
            </div>
          </div>
        ) : (
          <>
            {/* TOP HALF: Map */}
            <div className="h-[40%] bg-slate-950 relative border-b border-slate-800">
              {/* Optional: Add a title overlay on map */}
              <div className="absolute top-4 left-4 z-[1000] bg-slate-900/80 backdrop-blur border border-slate-700 p-3 rounded-lg shadow-xl">
                 <h2 className="text-lg font-bold text-white mb-1">{selectedCrisis.title}</h2>
                 <p className="text-sm text-slate-400">Impact Radius: {selectedCrisis.affected_radius_m}m</p>
              </div>

              {selectedCrisis.latitude ? (
                 <MapContainer 
                  center={[selectedCrisis.latitude, selectedCrisis.longitude]} 
                  zoom={14} 
                  className="w-full h-full"
                  zoomControl={false}
                >
                  <TileLayer
                    url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
                    attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
                  />
                  <Circle 
                    center={[selectedCrisis.latitude, selectedCrisis.longitude]}
                    radius={selectedCrisis.affected_radius_m || 500}
                    pathOptions={{ color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.2 }}
                  />
                </MapContainer>
              ) : (
                <div className="w-full h-full flex items-center justify-center text-slate-600">
                   No GPS coordinates provided for this crisis.
                </div>
              )}
            </div>

            {/* BOTTOM HALF: SOP Viewer & Orchestration */}
            <div className="flex-1 overflow-y-auto p-8 relative">
              <div className="max-w-4xl mx-auto">
                <div className="flex justify-between items-end mb-8">
                  <div>
                    <h2 className="text-2xl font-bold text-white mb-2">AutoGen Orchestration</h2>
                    <p className="text-slate-400">Retriever Agent (SOP Database) → Planner Agent (GPT-4o) → Human-in-the-Loop</p>
                  </div>
                  
                  {/* Action Buttons based on Phase */}
                  {(!selectedCrisis.phase || selectedCrisis.phase === 'RETRIEVAL' || selectedCrisis.phase === 'PLANNING') && (
                     <button 
                      onClick={() => triggerOrchestration(selectedCrisis.id)}
                      disabled={selectedCrisis.phase === 'PLANNING' || selectedCrisis.phase === 'RETRIEVAL'}
                      className="px-6 py-3 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg font-semibold shadow-lg shadow-blue-500/20 transition-all flex items-center gap-2"
                    >
                      {selectedCrisis.phase === 'PLANNING' || selectedCrisis.phase === 'RETRIEVAL' ? (
                        <><Activity className="animate-spin" size={18}/> Generating Plan...</>
                      ) : (
                        'Generate JSON SOP Plan'
                      )}
                    </button>
                  )}

                  {selectedCrisis.phase === 'HITL_REVIEW' && (
                    <div className="animate-pulse">
                      <button 
                        onClick={() => approvePlan(selectedCrisis.id)}
                        className="px-8 py-3 bg-green-600 hover:bg-green-500 text-white rounded-lg font-bold shadow-lg shadow-green-500/30 transition-all flex items-center gap-2 border border-green-400/50"
                      >
                       <CheckCircle2 size={18}/> APPROVE & DEPLOY
                      </button>
                    </div>
                  )}

                  {selectedCrisis.phase === 'EXECUTION' && (
                     <div className="px-6 py-3 bg-slate-800 text-green-400 rounded-lg font-semibold border border-green-500/30 flex items-center gap-2">
                       <CheckCircle2 size={18}/> Plan Active
                     </div>
                  )}
                </div>

                {/* Generated Plan Display */}
                {selectedCrisis.orchestration_state?.plan ? (
                  <div className="space-y-6">
                    {/* Summary Row */}
                    <div className="grid grid-cols-3 gap-4">
                      <div className="bg-slate-900 p-4 rounded-xl border border-slate-700/50">
                        <div className="text-xs text-slate-500 uppercase tracking-widest font-semibold mb-1">Target Phase</div>
                        <div className="text-lg text-white font-medium">{selectedCrisis.orchestration_state.plan.phase}</div>
                      </div>
                      <div className="bg-slate-900 p-4 rounded-xl border border-slate-700/50">
                        <div className="text-xs text-slate-500 uppercase tracking-widest font-semibold mb-1">Est. Population at Risk</div>
                        <div className="text-lg text-white font-medium">{selectedCrisis.orchestration_state.plan.estimated_affected_population?.toLocaleString() || 'Unknown'}</div>
                      </div>
                      <div className="bg-slate-900 p-4 rounded-xl border border-slate-700/50">
                        <div className="text-xs text-slate-500 uppercase tracking-widest font-semibold mb-1">Generated By</div>
                        <div className="text-lg text-blue-400 font-medium">AutoGen (GPT-4o)</div>
                      </div>
                    </div>

                    {/* Tasks List */}
                    <div className="bg-slate-900 rounded-xl border border-slate-700 overflow-hidden shadow-xl">
                      <div className="bg-slate-800/80 px-4 py-3 border-b border-slate-700 flex justify-between items-center">
                         <h3 className="font-semibold text-white">Prioritized Task Checklist</h3>
                         <span className="text-xs bg-slate-700 text-slate-300 px-2 py-1 rounded">
                           Based strictly on NDMA Manuals
                         </span>
                      </div>
                      <div className="divide-y divide-slate-800/50">
                        {selectedCrisis.orchestration_state.plan.tasks?.map((task: any) => (
                          <div key={task.id} className="p-4 hover:bg-slate-800/30 transition-colors flex gap-4">
                            <div className="mt-1">
                              <div className={classNames(
                                "w-2 h-2 rounded-full",
                                task.priority === 'CRITICAL' ? 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.8)]' :
                                task.priority === 'HIGH' ? 'bg-orange-500' :
                                task.priority === 'MEDIUM' ? 'bg-yellow-500' : 'bg-slate-500'
                              )} />
                            </div>
                            <div className="flex-1">
                              <div className="flex justify-between items-start mb-1">
                                {selectedCrisis.phase === 'HITL_REVIEW' ? (
                                  <div className="flex-1 mr-4">
                                    <textarea
                                      className="w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-slate-200 text-base focus:border-blue-500 focus:outline-none resize-none"
                                      rows={2}
                                      value={editingTasks[task.id] ?? task.action}
                                      onChange={(e) => setEditingTasks(prev => ({ ...prev, [task.id]: e.target.value }))}
                                    />
                                    {editingTasks[task.id] && editingTasks[task.id] !== task.action && (
                                      <div className="text-xs text-blue-400 mt-1 flex items-center gap-1">
                                        <Pencil size={10} /> Edited
                                      </div>
                                    )}
                                  </div>
                                ) : (
                                  <p className="text-slate-200 font-medium text-lg leading-snug">{task.action}</p>
                                )}
                                <span className="text-xs text-slate-500 border border-slate-700 rounded px-2 tracking-wide font-mono bg-slate-950/50 whitespace-nowrap ml-4">
                                  Ref: {task.sop_reference}
                                </span>
                              </div>
                              <div className="flex gap-4 mt-2 text-sm text-slate-400">
                                <span><strong className="text-slate-500">Zone:</strong> {task.zone}</span>
                                <span><strong className="text-slate-500">Resources:</strong> {task.resource_needed}</span>
                                <span><strong className="text-slate-500">Time:</strong> {task.estimated_time_minutes}m</span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                    
                    {/* Raw JSON Debug View */}
                    <div className="mt-8">
                       <details className="group">
                         <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-300 transition-colors select-none flex items-center gap-2">
                           <span className="group-open:rotate-90 transition-transform">▶</span>
                           View Raw JSON Output
                         </summary>
                         <pre className="mt-4 p-4 bg-slate-950 rounded-lg border border-slate-800 text-xs text-slate-400 overflow-x-auto font-mono">
                           {JSON.stringify(selectedCrisis.orchestration_state, null, 2)}
                         </pre>
                       </details>
                    </div>

                  </div>
                ) : (
                  <div className="h-64 rounded-xl border-2 border-dashed border-slate-700 flex flex-col items-center justify-center text-slate-500">
                    <ShieldAlert className="w-12 h-12 mb-4 opacity-50 text-slate-600" />
                    <p className="text-lg">No plan generated yet.</p>
                    <p className="text-sm mt-1">Click the generation button above to run AutoGen.</p>
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
