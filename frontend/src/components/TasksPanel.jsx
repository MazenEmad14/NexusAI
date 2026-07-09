import { useState, useEffect, useCallback } from 'react';
import { CheckCircle2, Clock, AlertCircle } from 'lucide-react';
import DashboardPanel from './DashboardPanel';
import { fetchTasks } from '../services/api';

export default function TasksPanel({ projectId = 'default' }) {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchTasks(projectId);
      setTasks(data?.data?.tasks || data?.tasks || []);
    } catch (err) {
      setError(err.message || 'Failed to load tasks');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const statusIcon = (status) => {
    if (status === 'completed') return <CheckCircle2 className="w-4 h-4 text-emerald-400" />;
    if (status === 'pending') return <Clock className="w-4 h-4 text-yellow-400" />;
    return <AlertCircle className="w-4 h-4 text-white/30" />;
  };

  return (
    <DashboardPanel
      title="✅ Tasks"
      loading={loading}
      error={error}
      onRetry={load}
      isEmpty={tasks.length === 0}
      emptyMessage="No pending tasks"
      emptyIcon={CheckCircle2}
    >
      {tasks.map((task, i) => (
        <div
          key={task.id || i}
          className="flex items-start gap-3 p-3.5 rounded-xl bg-white/[0.03] border border-white/[0.06] hover:bg-white/[0.05] transition-colors"
        >
          <div className="mt-0.5 shrink-0">{statusIcon(task.status)}</div>
          <div className="flex-1 min-w-0">
            <p className="text-sm text-white/80 m-0 leading-relaxed">{task.description}</p>
            <div className="flex items-center gap-3 mt-1.5">
              {task.deadline && (
                <span className="text-[10px] text-white/30 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {new Date(task.deadline).toLocaleDateString('en-US', {
                    month: 'short', day: 'numeric', year: 'numeric',
                    hour: '2-digit', minute: '2-digit'
                  })}
                </span>
              )}
              <span className={`text-[10px] px-2 py-0.5 rounded-full border ${task.status === 'pending'
                  ? 'bg-yellow-500/10 text-yellow-400/80 border-yellow-500/20'
                  : 'bg-emerald-500/10 text-emerald-400/80 border-emerald-500/20'
                }`}>
                {task.status}
              </span>
            </div>
          </div>
        </div>
      ))}
    </DashboardPanel>
  );
}
