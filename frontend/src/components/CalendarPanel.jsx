import { useState, useEffect, useCallback } from 'react';
import { Calendar, Clock, ExternalLink } from 'lucide-react';
import DashboardPanel from './DashboardPanel';
import { fetchCalendarEvents } from '../services/api';

export default function CalendarPanel() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [warning, setWarning] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setWarning(null);
    try {
      const data = await fetchCalendarEvents();
      setEvents(data?.data?.events || data?.events || []);
      if (data?.data?.warning || data?.warning) setWarning(data?.data?.warning || data?.warning);
    } catch (err) {
      setError(err.message || 'Failed to load events');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const formatEventTime = (isoString) => {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleString('en-US', {
        weekday: 'short', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit'
      });
    } catch {
      return isoString;
    }
  };

  return (
    <DashboardPanel
      title="📅 Calendar"
      loading={loading}
      error={error}
      onRetry={load}
      isEmpty={events.length === 0}
      emptyMessage={warning || "No upcoming events"}
      emptyIcon={Calendar}
    >
      {events.map((event, i) => (
        <div
          key={event.event_id || i}
          className="flex items-start gap-3 p-3.5 rounded-xl bg-white/[0.03] border border-white/[0.06] hover:bg-white/[0.05] transition-colors"
        >
          <div className="mt-0.5 shrink-0">
            <div className="w-8 h-8 rounded-lg bg-[#00f0ff]/10 border border-[#00f0ff]/20 flex items-center justify-center">
              <Calendar className="w-4 h-4 text-[#00f0ff]/80" />
            </div>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm text-white/85 m-0 font-medium">{event.title}</p>
            <div className="flex items-center gap-3 mt-1.5 flex-wrap">
              <span className="text-[10px] text-white/30 flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {formatEventTime(event.start)}
              </span>
              {event.end && (
                <span className="text-[10px] text-white/20">
                  → {formatEventTime(event.end)}
                </span>
              )}
            </div>
            {event.link && (
              <a
                href={event.link}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 mt-2 text-[10px] text-[#00f0ff]/60 hover:text-[#00f0ff] transition-colors"
              >
                <ExternalLink className="w-3 h-3" />
                Open in Calendar
              </a>
            )}
          </div>
        </div>
      ))}
    </DashboardPanel>
  );
}
