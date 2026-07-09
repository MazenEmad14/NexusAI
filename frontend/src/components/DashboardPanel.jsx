import { motion } from 'framer-motion';
import { Loader2, AlertTriangle, Inbox } from 'lucide-react';

/**
 * DashboardPanel — shared wrapper for all dashboard views.
 * Handles loading, error, and empty states consistently.
 */
export default function DashboardPanel({
  title,
  loading,
  error,
  onRetry,
  isEmpty,
  emptyMessage = 'No data available',
  emptyIcon: EmptyIcon = Inbox,
  children,
  headerAction,
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
      className="flex flex-col h-full"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-semibold text-white/90 m-0">{title}</h2>
        {headerAction}
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex-1 flex items-center justify-center">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="w-8 h-8 text-[#00f0ff]/60 animate-spin" />
            <p className="text-sm text-white/30">Loading…</p>
          </div>
        </div>
      )}

      {/* Error */}
      {!loading && error && (
        <div className="flex-1 flex items-center justify-center">
          <div className="flex flex-col items-center gap-3 text-center">
            <div className="w-12 h-12 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center justify-center">
              <AlertTriangle className="w-6 h-6 text-red-400" />
            </div>
            <p className="text-sm text-red-400/80">{error}</p>
            {onRetry && (
              <button
                onClick={onRetry}
                className="px-4 py-1.5 rounded-lg bg-white/5 border border-white/10 text-white/50 text-xs hover:bg-white/10 hover:text-white/70 transition-colors cursor-pointer"
              >
                Retry
              </button>
            )}
          </div>
        </div>
      )}

      {/* Empty */}
      {!loading && !error && isEmpty && (
        <div className="flex-1 flex items-center justify-center">
          <div className="flex flex-col items-center gap-3 text-center">
            <div className="w-12 h-12 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center">
              <EmptyIcon className="w-6 h-6 text-white/20" />
            </div>
            <p className="text-sm text-white/30">{emptyMessage}</p>
          </div>
        </div>
      )}

      {/* Content */}
      {!loading && !error && !isEmpty && (
        <div className="flex-1 overflow-y-auto space-y-3 pr-1">
          {children}
        </div>
      )}
    </motion.div>
  );
}
