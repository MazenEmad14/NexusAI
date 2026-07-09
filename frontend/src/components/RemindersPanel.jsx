import { useState, useEffect, useCallback } from 'react';
import { Bell, BellOff, Clock } from 'lucide-react';
import DashboardPanel from './DashboardPanel';
import { fetchReminders } from '../services/api';

export default function RemindersPanel({ projectId = 'default' }) {
  const [reminders, setReminders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchReminders(projectId);
      setReminders(data?.data?.reminders || data?.reminders || []);
    } catch (err) {
      setError(err.message || 'Failed to load reminders');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  return (
    <DashboardPanel
      title="🔔 Reminders"
      loading={loading}
      error={error}
      onRetry={load}
      isEmpty={reminders.length === 0}
      emptyMessage="No reminders set"
      emptyIcon={Bell}
    >
      {reminders.map((rem, i) => (
        <div
          key={rem.id || i}
          className={`flex items-start gap-3 p-3.5 rounded-xl border transition-colors ${rem.is_completed
              ? 'bg-white/[0.02] border-white/[0.04] opacity-50'
              : 'bg-white/[0.03] border-white/[0.06] hover:bg-white/[0.05]'
            }`}
        >
          <div className="mt-0.5 shrink-0">
            {rem.is_completed
              ? <BellOff className="w-4 h-4 text-white/25" />
              : <Bell className="w-4 h-4 text-yellow-400" />
            }
          </div>
          <div className="flex-1 min-w-0">
            <p className={`text-sm m-0 leading-relaxed ${rem.is_completed ? 'text-white/40 line-through' : 'text-white/80'}`}>
              {rem.task}
            </p>
            <div className="flex items-center gap-3 mt-1.5">
              {(rem.date || rem.time) && (
                <span className="text-[10px] text-white/30 flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {[rem.date, rem.time].filter(Boolean).join(' at ')}
                </span>
              )}
              {rem.notes && (
                <span className="text-[10px] text-white/25 truncate max-w-[200px]">
                  {rem.notes}
                </span>
              )}
            </div>
          </div>
        </div>
      ))}
    </DashboardPanel>
  );
}
