import { useState, useRef, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Sparkles,
  PenSquare,
  MessageSquare,
  FileText,
  Upload,
  Mail,
  Calendar,
  HardDrive,
  Settings,
  Paperclip,
  Mic,
  SendHorizontal,
  User,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Loader2,
  Wifi,
  WifiOff,
  X,
  Headphones,
  ListTodo,
  Bell,
  BarChart3,
  BookOpen,
  Brain,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';

// API
import {
  sendChatMessage,
  streamChatMessage,
  resumeAction,
  uploadFile,
  processFiles,
  pushIndex,
  healthCheck,
  servicesHealthCheck,
  fetchReminders,
  fetchCalendarEvents,
  fetchTasks,
  fetchDailySummary,
  getGoogleAuthUrl,
  checkGoogleAuthStatus,
  transcribeAudio,
  generateSpeech,
  openNotificationStream,
  syncTimezone
} from '../services/api';

// Hooks
import { useThread } from '../hooks/useThread';
import { useToast } from '../hooks/useToast';

// Components
import ToastContainer from '../components/ToastContainer';
import TasksPanel from '../components/TasksPanel';
import RemindersPanel from '../components/RemindersPanel';
import CalendarPanel from '../components/CalendarPanel';
import SummaryPanel from '../components/SummaryPanel';
import KnowledgePanel from '../components/KnowledgePanel';
import VoiceChatModal from '../components/VoiceChatModal';
import ChartRenderer from '../components/ChartRenderer';
import TTSButton from '../components/TTSButton';

/* ───────────────────────── constants ───────────────────────── */

const PROJECT_ID = 'default';

const agentLabels = {
  supervisor_thinking: 'Supervisor is routing your request…',
  rag_searching: 'RAG Agent is searching documents…',
  email_processing: 'Email Agent is processing inbox…',
  drafting: 'Draft Agent is composing response…',
  classifying: 'Classifying your request…',
  searching_kb: 'Searching Knowledge Base…',
  searching_gmail: 'Searching Gmail inbox…',
  extracting_actions: 'Extracting action details…',
  sending_email: 'Sending email…',
  creating_event: 'Creating calendar event…',
  saving_reminder: 'Saving reminder…',
  formatting: 'Formatting response…',
};

/** Sidebar dashboard tabs */
const dashboardTabs = [
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'tasks', label: 'Tasks', icon: ListTodo },
  { id: 'reminders', label: 'Reminders', icon: Bell },
  { id: 'calendar', label: 'Calendar', icon: Calendar },
  { id: 'summary', label: 'Summary', icon: BarChart3 },
  { id: 'knowledge', label: 'Knowledge', icon: BookOpen },
];

/** Map backend response type → agent badge list */
function getAgentBadges(type, source) {
  const badges = [
    { label: '🧠 Supervisor', color: 'bg-purple-500/20 text-purple-300 border-purple-500/30' },
  ];

  if (source === 'knowledge_base' || type === 'retrieval') {
    badges.push({ label: '📄 RAG Agent', color: 'bg-orange-500/20 text-orange-300 border-orange-500/30' });
  }
  if (source === 'gmail' || source === 'gmail_send' || type?.includes('send_email')) {
    badges.push({ label: '📧 Email Agent', color: 'bg-blue-500/20 text-blue-300 border-blue-500/30' });
  }
  if (source === 'calendar' || type?.includes('calendar')) {
    badges.push({ label: '📅 Calendar Agent', color: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' });
  }
  if (source === 'reminder' || type?.includes('reminder')) {
    badges.push({ label: '⏰ Reminder Agent', color: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30' });
  }
  if (source === 'daily_summary') {
    badges.push({ label: '📊 Summary Agent', color: 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30' });
  }

  return badges;
}

/* ───────────────────────── component ──────────────────────── */

export default function WorkspacePage() {
  /* ── thread persistence ── */
  const {
    sessions,
    activeSessionId,
    activeSession,
    threadId,
    createNewSession,
    updateSessionTitle,
    switchSession,
  } = useThread();

  /* ── toast notifications ── */
  const { toasts, showSuccess, showError, showInfo, dismissToast } = useToast();

  /* ── state ── */
  // Per-session message store: { sessionId: messages[] }
  const [messageStore, setMessageStore] = useState({});
  const messages = messageStore[activeSessionId] || [];
  const setMessages = useCallback((updater) => {
    setMessageStore(prev => {
      const current = prev[activeSessionId] || [];
      const next = typeof updater === 'function' ? updater(current) : updater;
      return { ...prev, [activeSessionId]: next };
    });
  }, [activeSessionId]);

  const [inputValue, setInputValue] = useState('');
  const [agentState, setAgentState] = useState(null);
  const [panelRefreshKey, setPanelRefreshKey] = useState(0);
  const [isRecording, setIsRecording] = useState(false);
  const [inputFocused, setInputFocused] = useState(false);
  const [backendOnline, setBackendOnline] = useState(null);
  const [servicesHealth, setServicesHealth] = useState({ database: 'unknown', gmail: 'unknown', calendar: 'unknown' });
  const [pendingAction, setPendingAction] = useState(null);
  const [uploadingFile, setUploadingFile] = useState(false);
  const [uploadedFilePath, setUploadedFilePath] = useState(null);
  const [activeTab, setActiveTab] = useState('chat');
  const [playingMessageId, setPlayingMessageId] = useState(null);
  const [isProcessingAudio, setIsProcessingAudio] = useState(false);
  const [isVoiceModeOpen, setIsVoiceModeOpen] = useState(false);
  
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const abortRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  const [googleConnected, setGoogleConnected] = useState(false);

  /* ── integrations status (derived from health check) ── */
  const integrations = [
    { name: 'Gmail', icon: Mail, connected: googleConnected },
    { name: 'Calendar', icon: Calendar, connected: googleConnected },
    { name: 'Drive', icon: HardDrive, connected: false },
  ];

  /* ── health check on mount ── */
  useEffect(() => {
    const check = async () => {
      const isOnline = await healthCheck();
      setBackendOnline(isOnline);
      if (isOnline) {
        const health = await servicesHealthCheck();
        setServicesHealth(health);
        try {
          const gAuth = await checkGoogleAuthStatus();
          setGoogleConnected(gAuth?.authenticated || false);
        } catch (err) {}
      }
    };
    check();
    const interval = setInterval(check, 30000);
    return () => clearInterval(interval);
  }, []);

  /* auto-scroll */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, agentState, pendingAction]);

  /* auto-resize textarea */
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 160)}px`;
    }
  }, [inputValue]);

  /* ── cleanup abort controller on unmount ── */
  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, []);

  /* ── tell the server our time zone (once per browser) ── */
  useEffect(() => { syncTimezone(); }, []);

  /* ── SSE notifications ── */
  useEffect(() => {
    let eventSource = null;
    let retryTimer = null;
    let cancelled = false;

    const connect = async () => {
      try {
        eventSource = await openNotificationStream();
      } catch {
        if (!cancelled) retryTimer = setTimeout(connect, 15000);
        return;
      }
      if (cancelled) {
        eventSource.close();
        return;
      }

      eventSource.onmessage = (event) => {
        if (!event.data) return;
        let content = event.data;
        try { content = JSON.parse(event.data); } catch { /* plain text */ }

        showInfo('New Proactive Alert');
        setMessages(prev => [...prev, {
          id: Date.now() + Math.random(),
          role: 'assistant',
          content,
          isProactive: true,
          agents: [{ label: 'Proactive', icon: Brain, color: 'text-amber-400', bg: 'bg-amber-400/10' }],
          autoPlayTTS: false
        }]);
      };

      // A ticket is single-use, so a dropped stream needs a fresh ticket, not the
      // browser's automatic reconnect.
      eventSource.onerror = () => {
        eventSource.close();
        if (!cancelled) retryTimer = setTimeout(connect, 5000);
      };
    };

    connect();

    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      if (eventSource) eventSource.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ── record audio ── */
  const handleMicToggle = async () => {
    if (isRecording) {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
        mediaRecorderRef.current.stop();
      }
      setIsRecording(false);
    } else {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mediaRecorder = new MediaRecorder(stream);
        mediaRecorderRef.current = mediaRecorder;
        audioChunksRef.current = [];

        mediaRecorder.ondataavailable = (e) => {
          if (e.data.size > 0) audioChunksRef.current.push(e.data);
        };

        mediaRecorder.onstop = async () => {
          const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
          setIsProcessingAudio(true);
          try {
            const transcribedText = await transcribeAudio(audioBlob);
            setInputValue(transcribedText);
            // Auto-send after transcription
            setTimeout(() => {
              handleSend(transcribedText, 'voice');
            }, 100);
          } catch (error) {
            showError(error.message || 'Failed to transcribe audio');
          } finally {
            setIsProcessingAudio(false);
            stream.getTracks().forEach(track => track.stop());
          }
        };

        mediaRecorder.start();
        setIsRecording(true);
      } catch (err) {
        showError('Microphone access denied or unavailable.');
      }
    }
  };

  /* ── send message ── */
  const handleSend = useCallback(async (overrideText = null, overrideMode = null) => {
    const text = (overrideText ?? inputValue).trim();
    if (!text || agentState) return;

    const mode = overrideMode ?? (isRecording ? 'voice' : 'text');
    if (isRecording && !overrideMode) setIsRecording(false);
    
    // Clear input if we used the input box
    if (!overrideText) setInputValue('');

    const dummyId = Date.now() + 1;
    // Add user message AND dummy assistant message immediately
    const userMsg = { id: Date.now(), role: 'user', content: text, inputMode: mode };
    const assistantMsg = {
      id: dummyId,
      role: 'assistant',
      content: '', 
      autoPlayTTS: mode === 'voice',
      isStreaming: true,
      agents: []
    };
    
    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setInputValue('');

    // Update session title from first message
    if (messages.length === 0) {
      const title = text.length > 35 ? text.slice(0, 35) + '…' : text;
      updateSessionTitle(activeSessionId, title);
    }

    // Create abort controller for this request
    abortRef.current = new AbortController();

    try {
      await streamChatMessage(
        text, 
        PROJECT_ID, 
        threadId,
        // onChunk
        (content) => {
          setMessages(prev => {
            const newArr = [...prev];
            const idx = newArr.findIndex(m => m.id === dummyId);
            if (idx !== -1) {
              newArr[idx] = { ...newArr[idx], content: newArr[idx].content + content };
            }
            return newArr;
          });
        },
        // onAction
        (actionDetails, actionType, metadata) => {
          setMessages(prev => {
            const newArr = [...prev];
            const idx = newArr.findIndex(m => m.id === dummyId);
            if (idx !== -1) {
              newArr[idx] = {
                ...newArr[idx],
                isPending: true,
                isStreaming: false,
                actionDetails,
                metadata,
                agents: getAgentBadges(`action:${actionType}`, metadata?.source),
                content: `Pending approval to execute action: ${actionType}`,
              };
            }
            return newArr;
          });
          setPendingAction({
            threadId: threadId,
            response: `Pending approval to execute action: ${actionType}`,
            type: actionType,
            details: actionDetails,
          });
          showInfo('⏳ Action requires your approval');
        },
        // onMetadata
        (metadata, chartData, status, actionType, requestType) => {
          setMessages(prev => {
            const newArr = [...prev];
            const idx = newArr.findIndex(m => m.id === dummyId);
            if (idx !== -1) {
              newArr[idx] = {
                ...newArr[idx],
                isStreaming: false,
                metadata,
                chartData,
                agents: getAgentBadges(metadata?.request_type || '', metadata?.source),
              };
            }
            return newArr;
          });

          // Trigger Toasts based on actions
          if (actionType === 'send_email') {
            if (status === 'success') showSuccess('Email sent successfully.');
            else if (status === 'error') showError('Failed to send email.');
          } else if (actionType === 'calendar' || actionType === 'create_calendar_event') {
            if (status === 'success') showSuccess('Calendar event created successfully.');
            else if (status === 'error') showError('Failed to create calendar event.');
          } else if (actionType === 'reminder' || actionType === 'save_reminder') {
            if (status === 'success') showSuccess('Reminder saved successfully.');
            else if (status === 'error') showError('Failed to save reminder.');
          } else if (requestType === 'retrieval' && metadata?.source === 'gmail') {
            if (status === 'success') showSuccess('Gmail search completed.');
            else if (status === 'no_results') showInfo('No emails found.');
            else if (status === 'error') showError('Gmail search failed.');
          }
        },
        abortRef.current.signal, 
        uploadedFilePath
      );
    } catch (err) {
      setAgentState(null);
      if (err.name === 'AbortError') {
        setMessages(prev => prev.filter(m => m.id !== dummyId));
        return;
      }

      setMessages(prev => {
        const newArr = [...prev];
        const idx = newArr.findIndex(m => m.id === dummyId);
        if (idx !== -1) {
          newArr[idx] = {
            ...newArr[idx],
            isStreaming: false,
            isError: true,
            content: `⚠️ **Error communicating with the backend:**\n\n\`${err.message}\``,
          };
        }
        return newArr;
      });
      
      // Clear file path even on error
      setUploadedFilePath(null);
      
      if (err.message && err.message.includes('Thread checkpoint invalid')) {
        showError('Invalid session checkpoint. Restarting chat...');
        createNewSession();
        setPendingAction(null);
        setActiveTab('chat');
      } else {
        showError(err.message || 'Connection failed. Is the backend running?');
      }
    }
  }, [inputValue, isRecording, agentState, messages, activeSessionId, updateSessionTitle, createNewSession, threadId, uploadedFilePath, showInfo, showError, getAgentBadges]);

  /* ── HITL approve / reject ── */
  const handleApproval = useCallback(async (approve) => {
    if (!pendingAction) return;

    setAgentState(approve ? 'sending_email' : null);

    // Create abort controller
    abortRef.current = new AbortController();

    try {
      const data = await resumeAction(pendingAction.threadId, approve, abortRef.current.signal);
      setAgentState(null);
      setPendingAction(null);

      const responseText = data?.response || data?.data?.response || '';
      const responseStatus = data?.status || data?.data?.status;
      const actionType = pendingAction.type;

      const resultMsg = {
        id: Date.now(),
        role: 'assistant',
        content: responseText || (approve ? '✅ Action executed successfully.' : '❌ Action cancelled.'),
        agents: approve
          ? [{ label: '✅ Executed', color: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' }]
          : [{ label: '❌ Cancelled', color: 'bg-red-500/20 text-red-300 border-red-500/30' }],
      };
      setMessages(prev => [...prev, resultMsg]);

      // Toast Notifications
      if (approve) {
        if (responseStatus === 'success') {
           if (actionType === 'send_email') showSuccess('Email sent successfully.');
           else if (actionType === 'create_calendar_event' || actionType === 'calendar') showSuccess('Calendar event created successfully.');
           else if (actionType === 'save_reminder' || actionType === 'reminder') showSuccess('Reminder saved successfully.');
           else showSuccess('Action executed successfully.');
        } else {
           showError('Failed to execute action.');
        }
      } else {
        showInfo('Action cancelled.');
      }

      if (approve) {
        // Force TasksPanel and RemindersPanel to re-mount and re-fetch after an action is approved.
        // (setTasks/setReminders don't exist here — each panel manages its own state internally.)
        setPanelRefreshKey(k => k + 1);
      }
    } catch (err) {
      setAgentState(null);
      setPendingAction(null);

      if (err.name === 'AbortError') return;

      setMessages(prev => [
        ...prev,
        { id: Date.now(), role: 'assistant', content: `⚠️ Error: \`${err.message}\``, isError: true },
      ]);
      showError(`Resume failed: ${err.message}`);
    }
  }, [pendingAction, setMessages, showSuccess, showError, showInfo]);

  /* ── file upload ── */
  const handleFileUpload = useCallback(async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploadingFile(true);

    try {
      const result = await uploadFile(file, PROJECT_ID);

      if (result.file_path) {
        setUploadedFilePath(result.file_path);
      }

      // Only text and PDF files go into the knowledge base; spreadsheets are used for data analysis.
      const indexable = result.indexable !== false;
      let indexed = false;

      if (indexable) {
        // Auto-process the uploaded file
        try {
          const processed = await processFiles(PROJECT_ID, { fileId: result.file_id });
          indexed = (processed?.processed_files ?? 0) > 0;
        } catch {
          // Processing might fail if vectorDB not connected
        }

        // Auto-push to vector DB
        if (indexed) {
          try {
            await pushIndex(PROJECT_ID);
          } catch {
            indexed = false; // Indexing might fail
          }
        }
      }

      // Notify in chat
      setMessages(prev => [
        ...prev,
        {
          id: Date.now(),
          role: 'assistant',
          content: indexed
            ? `📄 **File uploaded successfully:** \`${file.name}\`\n\nThe document has been added to your knowledge base and is now searchable.`
            : indexable
              ? `📄 **File uploaded:** \`${file.name}\`\n\nIt could not be added to the knowledge base (no readable text was found, or indexing is unavailable).`
              : `📊 **File uploaded:** \`${file.name}\`\n\nThis is a data file, so ask me to analyse it (for example "plot sales by region") instead of searching it.`,
          agents: [{ label: '📄 RAG Agent', color: 'bg-orange-500/20 text-orange-300 border-orange-500/30' }],
        },
      ]);
      showSuccess(indexed ? `"${file.name}" uploaded and indexed` : `"${file.name}" uploaded`);
    } catch (err) {
      setMessages(prev => [
        ...prev,
        {
          id: Date.now(),
          role: 'assistant',
          content: `⚠️ **File upload failed:** \`${err.message}\``,
          isError: true,
        },
      ]);
      showError(`Upload failed: ${err.message}`);
    } finally {
      setUploadingFile(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }, [setMessages, showSuccess, showError]);

  /* ── file upload from chat attachment ── */
  const handleChatAttachment = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  /* ── keyboard ── */
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  /* ── new chat ── */
  const handleNewChat = useCallback(() => {
    createNewSession();
    setPendingAction(null);
    setAgentState(null);
    setActiveTab('chat');
  }, [createNewSession]);

  /* ── switch session ── */
  const handleSwitchSession = useCallback((sessionId) => {
    switchSession(sessionId);
    setPendingAction(null);
    setAgentState(null);
    setActiveTab('chat');
  }, [switchSession]);

  /* ── toast callback for KnowledgePanel ── */
  const handlePanelToast = useCallback((variant, message) => {
    if (variant === 'success') showSuccess(message);
    else if (variant === 'error') showError(message);
    else showInfo(message);
  }, [showSuccess, showError, showInfo]);

  /* ───────────────────── render ───────────────────────── */
  return (
    <div className="flex h-screen w-full bg-[#0a0a0a] text-white overflow-hidden">
      {/* Toast notifications */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        accept=".pdf,.txt,.md,.docx,.csv"
        onChange={handleFileUpload}
      />

      {/* ═══════════ SIDEBAR ═══════════ */}
      <aside className="flex flex-col w-[280px] min-w-[280px] bg-white/[0.03] border-r border-white/10">
        {/* ── header ── */}
        <div className="flex items-center justify-between px-5 py-5">
          <Link to="/" className="flex items-center gap-2 no-underline">
            <div className="relative">
              <Sparkles className="w-6 h-6 text-[#00f0ff]" />
              <div className="absolute inset-0 blur-lg bg-[#00f0ff]/30 rounded-full" />
            </div>
            <span className="text-lg font-bold tracking-tight bg-gradient-to-r from-[#00f0ff] to-[#a855f7] bg-clip-text text-transparent">
              NexusAI
            </span>
          </Link>

          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={handleNewChat}
            className="p-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-white/60 hover:text-[#00f0ff] transition-colors cursor-pointer"
          >
            <PenSquare className="w-4 h-4" />
          </motion.button>
        </div>

        {/* ── backend status ── */}
        <div className="px-5 mb-3">
          <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs ${backendOnline === null
              ? 'bg-yellow-500/10 text-yellow-400/70'
              : backendOnline
                ? 'bg-emerald-500/10 text-emerald-400/70'
                : 'bg-red-500/10 text-red-400/70'
            }`}>
            {backendOnline === null ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : backendOnline ? (
              <Wifi className="w-3 h-3" />
            ) : (
              <WifiOff className="w-3 h-3" />
            )}
            {backendOnline === null
              ? 'Checking backend…'
              : backendOnline
                ? 'Backend connected'
                : 'Backend offline — port 5000'}
          </div>
        </div>

        {/* ── dashboard tabs ── */}
        <div className="px-4 mt-1">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2 px-1">
            Dashboard
          </p>
          <div className="flex flex-wrap gap-1">
            {dashboardTabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <motion.button
                  key={tab.id}
                  whileHover={{ scale: 1.03 }}
                  whileTap={{ scale: 0.97 }}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all cursor-pointer border ${isActive
                      ? 'bg-[#00f0ff]/10 text-[#00f0ff] border-[#00f0ff]/25'
                      : 'bg-transparent text-white/40 border-transparent hover:bg-white/5 hover:text-white/60'
                    }`}
                >
                  <Icon className="w-3 h-3" />
                  {tab.label}
                </motion.button>
              );
            })}
          </div>
        </div>

        {/* ── recent sessions ── */}
        <div className="px-4 mt-5">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2 px-1">
            Recent Sessions
          </p>
          <ul className="space-y-0.5 list-none m-0 p-0 max-h-[200px] overflow-y-auto">
            {sessions.map((s) => (
              <motion.li
                key={s.id}
                whileHover={{ x: 2 }}
                onClick={() => handleSwitchSession(s.id)}
                className={`flex items-center gap-2.5 px-3 py-2 rounded-lg cursor-pointer text-sm transition-colors ${s.id === activeSessionId
                    ? 'bg-white/[0.06] text-white border-l-2 border-[#00f0ff]'
                    : 'text-white/50 hover:bg-white/[0.04] hover:text-white/70 border-l-2 border-transparent'
                  }`}
              >
                <MessageSquare className="w-4 h-4 shrink-0" />
                <span className="truncate">{s.title}</span>
              </motion.li>
            ))}
          </ul>
        </div>

        {/* ── integrations ── */}
        <div className="mt-auto px-4 pb-5">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-white/30 mb-2 px-1">
            Integrations
          </p>
          <ul className="space-y-1 list-none m-0 p-0">
            {integrations.map((intg) => {
              const Icon = intg.icon;
              return (
                <li
                  key={intg.name}
                  className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm text-white/50"
                >
                  <Icon className="w-4 h-4 shrink-0" />
                  <span>{intg.name}</span>
                  <span className="ml-auto flex items-center gap-1.5 text-[10px]">
                    <span
                      className={`w-1.5 h-1.5 rounded-full ${intg.connected
                          ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,.6)]'
                          : 'bg-red-400 shadow-[0_0_6px_rgba(248,113,113,.5)]'
                        }`}
                    />
                    <span className={intg.connected ? 'text-emerald-400/70' : 'text-red-400/70'}>
                      {intg.connected ? 'Connected' : 'Disconnected'}
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
          
          <button
            onClick={async () => {
              try {
                const data = await getGoogleAuthUrl();
                if (data && data.auth_url) {
                  window.location.href = data.auth_url;
                }
              } catch (err) {
                showError("Failed to load Google Auth URL");
              }
            }}
            className="w-full mt-3 flex items-center justify-center gap-2 py-2 px-3 bg-white/[0.05] hover:bg-white/[0.08] text-white/70 hover:text-white rounded-lg text-sm transition-colors border border-white/10"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24">
              <path fill="currentColor" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
              <path fill="currentColor" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="currentColor" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
              <path fill="currentColor" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
            </svg>
            Connect Google
          </button>
        </div>
      </aside>

      {/* ═══════════ MAIN AREA ═══════════ */}
      <main className="flex flex-col flex-1 min-w-0">
        {/* ── top bar ── */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.06]">
          <div className="flex items-center gap-3">
            <div className={`w-2 h-2 rounded-full ${backendOnline ? 'bg-[#00f0ff] shadow-[0_0_8px_rgba(0,240,255,.5)]' : 'bg-red-400 shadow-[0_0_8px_rgba(248,113,113,.5)]'}`} />
            <h1 className="text-base font-semibold text-white/90 m-0 leading-normal">
              {activeTab === 'chat'
                ? (activeSession?.title || 'New Conversation')
                : dashboardTabs.find(t => t.id === activeTab)?.label || 'Dashboard'
              }
            </h1>
          </div>

          <div className="flex items-center gap-2">
            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              className="p-2 rounded-lg hover:bg-white/5 text-white/40 hover:text-white/70 transition-colors cursor-pointer bg-transparent border-none"
            >
              <Settings className="w-4.5 h-4.5" />
            </motion.button>
          </div>
        </div>

        {/* ═══════════ CONTENT AREA ═══════════ */}

        {/* ── Dashboard Panels (non-chat tabs) ── */}
        {activeTab !== 'chat' && (
          <div className="flex-1 overflow-y-auto px-6 py-6">
            <div className="max-w-3xl mx-auto h-full">
              {activeTab === 'tasks' && <TasksPanel key={panelRefreshKey} projectId={PROJECT_ID} />}
              {activeTab === 'reminders' && <RemindersPanel key={panelRefreshKey} projectId={PROJECT_ID} />}
              {activeTab === 'calendar' && <CalendarPanel />}
              {activeTab === 'summary' && <SummaryPanel projectId={PROJECT_ID} />}
              {activeTab === 'knowledge' && <KnowledgePanel projectId={PROJECT_ID} onToast={handlePanelToast} />}
            </div>
          </div>
        )}

        {/* ── Chat View ── */}
        {activeTab === 'chat' && (
          <>
            {/* ── messages ── */}
            <div className="flex-1 overflow-y-auto px-6 py-6 scroll-smooth">
              <div className="max-w-3xl mx-auto space-y-6">
                {/* Welcome message when empty */}
                {messages.length === 0 && (
                  <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="flex flex-col items-center justify-center py-20 text-center"
                  >
                    <div className="relative mb-6">
                      <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-[#00f0ff]/20 to-[#a855f7]/20 border border-white/10 flex items-center justify-center">
                        <Sparkles className="w-10 h-10 text-[#00f0ff]" />
                      </div>
                      <div className="absolute inset-0 blur-2xl bg-[#00f0ff]/10 rounded-full" />
                    </div>
                    <h2 className="text-2xl font-bold text-white/90 mb-2">Welcome to NexusAI</h2>
                    <p className="text-white/40 text-sm max-w-md leading-relaxed">
                      Your autonomous AI secretary is ready. Ask me to summarize emails,
                      search your knowledge base, schedule meetings, or set reminders.
                    </p>
                    {!backendOnline && backendOnline !== null && (
                      <div className="mt-4 flex items-center gap-2 px-4 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs">
                        <AlertTriangle className="w-3.5 h-3.5" />
                        Backend server is offline. Start it with: uvicorn src.main:app --port 5000
                      </div>
                    )}
                  </motion.div>
                )}

                {messages.map((msg, idx) => (
                  <motion.div
                    key={msg.id}
                    initial={{ opacity: 0, y: 16 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.35, delay: idx < 4 ? idx * 0.07 : 0 }}
                    className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                  >
                    {/* avatar – assistant */}
                    {msg.role === 'assistant' && (
                      <div className="shrink-0 mt-1 relative">
                        {playingMessageId === msg.id && (
                          <motion.div
                            animate={{ scale: [1, 1.4, 1], opacity: [0.4, 0.8, 0.4] }}
                            transition={{ repeat: Infinity, duration: 1.5, ease: "easeInOut" }}
                            className="absolute -inset-1 rounded-xl bg-[#00f0ff]/40 blur-[8px]"
                          />
                        )}
                        <div className={`relative z-10 w-8 h-8 rounded-xl border border-white/10 flex items-center justify-center ${msg.isError
                            ? 'bg-red-500/20'
                            : 'bg-gradient-to-br from-[#00f0ff]/20 to-[#a855f7]/20'
                          }`}>
                          {msg.isError ? (
                            <AlertTriangle className="w-4 h-4 text-red-400" />
                          ) : (
                            <Sparkles className="w-4 h-4 text-[#00f0ff]" />
                          )}
                        </div>
                      </div>
                    )}

                    {/* bubble */}
                    <div
                      className={`max-w-[85%] ${msg.role === 'user'
                          ? 'rounded-2xl rounded-tr-md px-5 py-3 bg-gradient-to-br from-[#00f0ff]/[0.12] to-[#a855f7]/[0.10] border border-[#00f0ff]/20 text-white/90 text-sm leading-relaxed'
                          : `rounded-2xl rounded-tl-md px-5 py-4 border text-white/80 ${msg.isError
                            ? 'bg-red-500/[0.06] border-red-500/20'
                            : msg.isPending
                              ? 'bg-yellow-500/[0.04] border-yellow-500/15'
                              : 'bg-white/[0.04] border-white/[0.06]'
                          }`
                        }`}
                    >
                      {/* agent badges */}
                      {msg.agents && (
                        <div className="flex flex-wrap gap-1.5 mb-3">
                          {msg.agents.map((a) => (
                            <span
                              key={a.label}
                              className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-medium border ${a.color}`}
                            >
                              {a.label}
                            </span>
                          ))}
                        </div>
                      )}

                      {/* content */}
                      {msg.role === 'assistant' ? (
                        <div className="markdown-content">
                          <ReactMarkdown
                            components={{
                              img: ({node, ...props}) => <img className="max-w-full rounded-lg mt-3 border border-white/10 shadow-lg" {...props} />
                            }}
                          >
                            {msg.content}
                          </ReactMarkdown>
                          {msg.chartData && (
                            <ChartRenderer chartData={msg.chartData} />
                          )}
                        </div>
                      ) : (
                        <p className="m-0">{msg.content}</p>
                      )}

                      {/* HITL action details */}
                      {msg.isPending && msg.actionDetails && Object.keys(msg.actionDetails).length > 0 && (
                        <div className="mt-3 p-3 rounded-lg bg-white/[0.03] border border-white/[0.06] text-xs">
                          <p className="text-white/50 font-medium mb-2">📋 Action Details:</p>
                          {Object.entries(msg.actionDetails).map(([key, val]) => (
                            <div key={key} className="flex gap-2 mb-1">
                              <span className="text-white/30 capitalize">{key}:</span>
                              <span className="text-white/60">{String(val)}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* TTS Button */}
                    {msg.role === 'assistant' && !msg.isError && !msg.isPending && (
                      <div className="shrink-0 mt-1.5">
                        <TTSButton 
                          text={msg.content} 
                          autoPlay={msg.autoPlayTTS} 
                          onPlayStateChange={(playing) => setPlayingMessageId(playing ? msg.id : null)}
                        />
                      </div>
                    )}

                    {/* avatar – user */}
                    {msg.role === 'user' && (
                      <div className="shrink-0 mt-1">
                        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-[#a855f7]/25 to-[#00f0ff]/15 border border-white/10 flex items-center justify-center">
                          <User className="w-4 h-4 text-white/70" />
                        </div>
                      </div>
                    )}
                  </motion.div>
                ))}

                {/* scroll anchor */}
                <div ref={messagesEndRef} />
              </div>
            </div>

            {/* ── HITL Approval Bar ── */}
            <AnimatePresence>
              {pendingAction && (
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 20 }}
                  className="flex justify-center px-6 mb-2"
                >
                  <div className="inline-flex items-center gap-3 px-5 py-3 rounded-xl bg-yellow-500/[0.08] border border-yellow-500/20 text-sm">
                    <AlertTriangle className="w-4 h-4 text-yellow-400" />
                    <span className="text-yellow-300/80">Action requires your approval</span>
                    <motion.button
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      onClick={() => handleApproval(true)}
                      disabled={!!agentState}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/30 text-emerald-300 text-xs font-medium cursor-pointer transition-colors disabled:opacity-40"
                    >
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      Approve
                    </motion.button>
                    <motion.button
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      onClick={() => handleApproval(false)}
                      disabled={!!agentState}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 text-red-300 text-xs font-medium cursor-pointer transition-colors disabled:opacity-40"
                    >
                      <XCircle className="w-3.5 h-3.5" />
                      Reject
                    </motion.button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* ── active agent indicator ── */}
            <AnimatePresence>
              {agentState && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 10 }}
                  transition={{ duration: 0.25 }}
                  className="flex justify-center px-6"
                >
                  <div className="inline-flex items-center gap-2.5 px-4 py-2 rounded-full bg-white/[0.04] border border-white/10 text-xs text-white/50 mb-2">
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#00f0ff] opacity-75" />
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-[#00f0ff]" />
                    </span>
                    {agentLabels[agentState] || 'Processing…'}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* ── input area ── */}
            <div className="px-6 pb-5 pt-2">
              <motion.div
                animate={{
                  boxShadow: inputFocused
                    ? '0 0 30px rgba(0,240,255,.08), 0 0 60px rgba(168,85,247,.05)'
                    : '0 0 0px transparent',
                  borderColor: inputFocused
                    ? 'rgba(0,240,255,.2)'
                    : 'rgba(255,255,255,.08)',
                }}
                transition={{ duration: 0.3 }}
                className="max-w-3xl mx-auto flex items-end gap-2 px-4 py-3 rounded-2xl bg-white/[0.04] backdrop-blur-xl border border-white/[0.08]"
              >
                {/* attachment */}
                <motion.button
                  whileHover={{ scale: 1.1 }}
                  whileTap={{ scale: 0.9 }}
                  onClick={handleChatAttachment}
                  disabled={uploadingFile}
                  className="shrink-0 p-2 rounded-lg text-white/30 hover:text-white/60 hover:bg-white/5 transition-colors cursor-pointer bg-transparent border-none disabled:opacity-30"
                >
                  {uploadingFile ? (
                    <Loader2 className="w-4.5 h-4.5 animate-spin" />
                  ) : (
                    <Paperclip className="w-4.5 h-4.5" />
                  )}
                </motion.button>

                {/* textarea */}
                <textarea
                  ref={textareaRef}
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onFocus={() => setInputFocused(true)}
                  onBlur={() => setInputFocused(false)}
                  placeholder={backendOnline ? 'Ask your AI secretary anything…' : 'Backend offline — start the server first…'}
                  rows={1}
                  className="flex-1 bg-transparent text-sm text-white/90 placeholder-white/25 outline-none resize-none leading-relaxed max-h-40 py-1.5 border-none"
                />

                {/* mic */}
                <motion.button
                  whileHover={{ scale: 1.1 }}
                  whileTap={{ scale: 0.9 }}
                  onClick={handleMicToggle}
                  disabled={isProcessingAudio}
                  className={`shrink-0 p-2 rounded-lg transition-colors cursor-pointer bg-transparent border-none ${isRecording || isProcessingAudio
                      ? 'text-red-400'
                      : 'text-white/30 hover:text-white/60 hover:bg-white/5'
                    } disabled:opacity-50`}
                >
                  <div className="relative">
                    {isProcessingAudio ? <Loader2 className="w-4.5 h-4.5 animate-spin" /> : <Mic className="w-4.5 h-4.5" />}
                    {isRecording && (
                      <motion.div
                        animate={{ scale: [1, 1.8, 1], opacity: [0.5, 0, 0.5] }}
                        transition={{ repeat: Infinity, duration: 1.4 }}
                        className="absolute inset-0 rounded-full bg-red-400/40"
                      />
                    )}
                  </div>
                </motion.button>

                {/* voice mode button */}
                <motion.button
                  whileHover={{ scale: 1.1 }}
                  whileTap={{ scale: 0.9 }}
                  onClick={() => setIsVoiceModeOpen(true)}
                  className="shrink-0 p-2 rounded-lg transition-colors cursor-pointer bg-transparent border-none text-white/30 hover:text-white/60 hover:bg-white/5"
                  title="Voice Chat Mode"
                >
                  <Headphones className="w-4.5 h-4.5" />
                </motion.button>

                {/* send */}
                <motion.button
                  whileHover={{ scale: 1.05 }}
                  whileTap={{ scale: 0.92 }}
                  onClick={() => handleSend()}
                  disabled={!inputValue.trim() || !!agentState}
                  className="shrink-0 p-2.5 rounded-xl bg-gradient-to-br from-[#00f0ff] to-[#00b8d4] text-[#0a0a0a] shadow-[0_0_16px_rgba(0,240,255,.25)] hover:shadow-[0_0_24px_rgba(0,240,255,.4)] transition-shadow cursor-pointer border-none disabled:opacity-30 disabled:cursor-not-allowed disabled:shadow-none"
                >
                  <SendHorizontal className="w-4 h-4" />
                </motion.button>
              </motion.div>

              <p className="text-center text-[10px] text-white/15 mt-2.5">
                NexusAI may produce inaccurate information. Always verify important details.
              </p>
            </div>
          </>
        )}
      </main>
      <VoiceChatModal 
        isOpen={isVoiceModeOpen} 
        onClose={() => setIsVoiceModeOpen(false)} 
        projectId={PROJECT_ID}
        threadId={threadId}
      />
    </div>
  );
}
