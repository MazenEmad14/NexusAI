import { useState, useEffect, useCallback } from 'react';
import { RefreshCw, BarChart3 } from 'lucide-react';
import { motion } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import DashboardPanel from './DashboardPanel';
import { fetchDailySummary } from '../services/api';

export default function SummaryPanel({ projectId = 'default' }) {
  const [summary, setSummary] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchDailySummary(projectId);
      setSummary(data?.data?.summary || data?.summary || '');
    } catch (err) {
      setError(err.message || 'Failed to generate summary');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  return (
    <DashboardPanel
      title="📊 Daily Summary"
      loading={loading}
      error={error}
      onRetry={load}
      isEmpty={!summary}
      emptyMessage="No summary available"
      emptyIcon={BarChart3}
      headerAction={
        <motion.button
          whileHover={{ scale: 1.05, rotate: 90 }}
          whileTap={{ scale: 0.95 }}
          onClick={load}
          disabled={loading}
          className="p-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-white/40 hover:text-[#00f0ff] transition-colors cursor-pointer disabled:opacity-30"
        >
          <RefreshCw className="w-4 h-4" />
        </motion.button>
      }
    >
      <div className="p-4 rounded-xl bg-white/[0.03] border border-white/[0.06]">
        <div className="markdown-content">
          <ReactMarkdown>{summary}</ReactMarkdown>
        </div>
      </div>
    </DashboardPanel>
  );
}
