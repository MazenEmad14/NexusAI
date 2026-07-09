import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle2, XCircle, Info, X } from 'lucide-react';

const variantConfig = {
  success: {
    icon: CheckCircle2,
    bg: 'bg-emerald-500/15',
    border: 'border-emerald-500/30',
    text: 'text-emerald-300',
    iconColor: 'text-emerald-400',
  },
  error: {
    icon: XCircle,
    bg: 'bg-red-500/15',
    border: 'border-red-500/30',
    text: 'text-red-300',
    iconColor: 'text-red-400',
  },
  info: {
    icon: Info,
    bg: 'bg-[#00f0ff]/10',
    border: 'border-[#00f0ff]/30',
    text: 'text-[#00f0ff]/90',
    iconColor: 'text-[#00f0ff]',
  },
};

export default function ToastContainer({ toasts, onDismiss }) {
  return (
    <div className="fixed top-5 right-5 z-[9999] flex flex-col gap-2 pointer-events-none">
      <AnimatePresence>
        {toasts.map((toast) => {
          const cfg = variantConfig[toast.variant] || variantConfig.info;
          const Icon = cfg.icon;

          return (
            <motion.div
              key={toast.id}
              initial={{ opacity: 0, x: 60, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 60, scale: 0.95 }}
              transition={{ duration: 0.3 }}
              className={`pointer-events-auto flex items-center gap-3 px-4 py-3 rounded-xl border backdrop-blur-xl shadow-lg min-w-[280px] max-w-[400px] ${cfg.bg} ${cfg.border}`}
            >
              <Icon className={`w-5 h-5 shrink-0 ${cfg.iconColor}`} />
              <p className={`text-sm flex-1 m-0 ${cfg.text}`}>{toast.message}</p>
              <button
                onClick={() => onDismiss(toast.id)}
                className="shrink-0 p-1 rounded-md hover:bg-white/10 text-white/40 hover:text-white/70 transition-colors cursor-pointer bg-transparent border-none"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
