import { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, User, Globe, Mail, Calendar, HardDrive, LogOut, Trash2, Link2, Unlink } from 'lucide-react';

import { useAuth } from '../hooks/useAuth';
import { disconnectGoogle, getGoogleAuthUrl, updateTimezone } from '../services/api';

const FALLBACK_ZONES = [
  'Africa/Cairo', 'Africa/Johannesburg', 'Africa/Lagos', 'America/Chicago', 'America/Los_Angeles', 'America/New_York',
  'America/Sao_Paulo', 'Asia/Dubai', 'Asia/Kolkata', 'Asia/Riyadh', 'Asia/Shanghai', 'Asia/Tokyo',
  'Australia/Sydney', 'Europe/Berlin', 'Europe/Istanbul', 'Europe/London', 'Europe/Paris', 'UTC',
];

function browserZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

function timeZones(current) {
  let zones;
  try {
    zones = Intl.supportedValuesOf('timeZone');
  } catch {
    zones = FALLBACK_ZONES;
  }
  return zones.includes(current) ? zones : [current, ...zones];
}

const SERVICES = [
  { key: 'gmail', label: 'Gmail', icon: Mail },
  { key: 'calendar', label: 'Calendar', icon: Calendar },
  { key: 'drive', label: 'Drive', icon: HardDrive },
];

/** Account, time zone, Google connection and local data, in one dialog opened from the workspace header. */
export default function SettingsModal({ open, onClose, googleConnected, googleServices, onGoogleChanged, showSuccess, showError }) {
  const { user, signOut } = useAuth();
  const [zone, setZone] = useState(user?.timezone || browserZone());
  const [savingZone, setSavingZone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const zones = useMemo(() => timeZones(zone), [zone]);

  useEffect(() => {
    if (!open) return undefined;
    setZone(user?.timezone || browserZone());
    setConfirmClear(false);
    const onKey = (event) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, user, onClose]);

  const saveZone = async (next) => {
    setZone(next);
    setSavingZone(true);
    try {
      await updateTimezone(next);
      showSuccess(`Time zone set to ${next}.`);
    } catch (err) {
      showError(err?.message || 'Could not save the time zone.');
    } finally {
      setSavingZone(false);
    }
  };

  const connect = async () => {
    setBusy(true);
    try {
      const data = await getGoogleAuthUrl();
      if (data?.auth_url) window.location.href = data.auth_url;
    } catch {
      showError('Failed to start the Google connection.');
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      await disconnectGoogle();
      showSuccess('Google disconnected.');
      onGoogleChanged();
    } catch (err) {
      showError(err?.message || 'Could not disconnect Google.');
    } finally {
      setBusy(false);
    }
  };

  const clearHistory = () => {
    try {
      localStorage.removeItem('nexusai_sessions');
      localStorage.removeItem('nexusai_active_session');
    } catch { /* storage unavailable: nothing to clear */ }
    window.location.reload();
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm px-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Settings"
            className="w-full max-w-md rounded-2xl border border-white/10 bg-[#111] p-6 shadow-2xl max-h-[90vh] overflow-y-auto"
            initial={{ opacity: 0, y: 20, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 10, scale: 0.98 }}
            transition={{ duration: 0.2 }}
          >
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-lg font-semibold text-white m-0">Settings</h2>
              <button onClick={onClose} aria-label="Close settings"
                className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/5 bg-transparent border-none cursor-pointer">
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Account */}
            <section className="mb-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">Account</p>
              <div className="flex items-center gap-3 rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-br from-[#00f0ff]/30 to-[#a855f7]/30">
                  <User className="w-5 h-5 text-white/80" />
                </div>
                <div className="min-w-0">
                  <p className="m-0 text-sm font-medium text-white truncate">{user?.name || 'Your account'}</p>
                  <p className="m-0 text-xs text-white/40 truncate">{user?.email}</p>
                </div>
              </div>
            </section>

            {/* Time zone */}
            <section className="mb-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">Time zone</p>
              <div className="flex items-center gap-2 rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
                <Globe className="w-4 h-4 text-white/40 shrink-0" />
                <select
                  value={zone}
                  disabled={savingZone}
                  onChange={(event) => saveZone(event.target.value)}
                  aria-label="Time zone"
                  className="flex-1 min-w-0 bg-transparent text-sm text-white/80 outline-none border-none cursor-pointer"
                >
                  {zones.map((name) => (
                    <option key={name} value={name} className="bg-[#111]">{name}</option>
                  ))}
                </select>
              </div>
              <p className="mt-1.5 mb-0 text-xs text-white/30">Used for "tomorrow", reminders and the morning briefing.</p>
            </section>

            {/* Google */}
            <section className="mb-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">Google</p>
              <div className="rounded-xl bg-white/[0.03] border border-white/[0.06] p-3">
                <ul className="list-none m-0 p-0 space-y-2 mb-3">
                  {SERVICES.map(({ key, label, icon: Icon }) => {
                    const on = googleConnected && googleServices?.[key];
                    return (
                      <li key={key} className="flex items-center gap-2.5 text-sm text-white/60">
                        <Icon className="w-4 h-4" />
                        <span>{label}</span>
                        <span className={`ml-auto text-xs ${on ? 'text-emerald-400/80' : googleConnected ? 'text-amber-400/80' : 'text-red-400/70'}`}>
                          {on ? 'Allowed' : googleConnected ? 'Reconnect to allow' : 'Not connected'}
                        </span>
                      </li>
                    );
                  })}
                </ul>
                <div className="flex gap-2">
                  <button onClick={connect} disabled={busy}
                    className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-white/80 hover:bg-white/10 cursor-pointer disabled:opacity-50">
                    <Link2 className="w-4 h-4" />
                    {googleConnected ? 'Reconnect' : 'Connect Google'}
                  </button>
                  {googleConnected && (
                    <button onClick={disconnect} disabled={busy}
                      className="inline-flex items-center justify-center gap-2 rounded-lg border border-red-400/20 bg-red-500/5 px-3 py-2 text-sm text-red-300/80 hover:bg-red-500/10 cursor-pointer disabled:opacity-50">
                      <Unlink className="w-4 h-4" />
                      Disconnect
                    </button>
                  )}
                </div>
              </div>
            </section>

            {/* Local data */}
            <section className="mb-6">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2">This browser</p>
              <button
                onClick={() => (confirmClear ? clearHistory() : setConfirmClear(true))}
                className={`w-full inline-flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm cursor-pointer ${
                  confirmClear ? 'border-red-400/40 bg-red-500/10 text-red-300' : 'border-white/10 bg-white/5 text-white/70 hover:bg-white/10'
                }`}
              >
                <Trash2 className="w-4 h-4" />
                {confirmClear ? 'Click again to clear the conversation list' : 'Clear conversation list'}
              </button>
              <p className="mt-1.5 mb-0 text-xs text-white/30">Removes the chat list saved in this browser. Your reminders, tasks and documents are kept.</p>
            </section>

            <button onClick={signOut}
              className="w-full inline-flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-[#00f0ff]/90 to-[#3b82f6]/90 px-3 py-2.5 text-sm font-semibold text-[#0a0a0a] cursor-pointer border-none">
              <LogOut className="w-4 h-4" />
              Sign out
            </button>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
