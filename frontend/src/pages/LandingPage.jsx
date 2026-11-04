import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Brain, Mail, Globe, Mic, ArrowRight, Sparkles, Zap, Calendar, HardDrive, BellRing, BarChart3,
  ShieldCheck, UserCheck, Lock, Languages, Search, Route, CheckCircle2,
} from "lucide-react";
import { Link } from "react-router-dom";

/* ─── What the assistant can do ─── */
const features = [
  { icon: Brain, title: "Knowledge base", accent: "#00f0ff",
    desc: "Upload PDFs and notes. Ask in plain language and get answers drawn from your own documents." },
  { icon: Mail, title: "Gmail", accent: "#a855f7",
    desc: "Find, summarise and draft email. Anything that sends a message waits for your approval." },
  { icon: Calendar, title: "Calendar", accent: "#10b981",
    desc: "Say when and with whom. It works out the exact date in your time zone and books the event once you approve." },
  { icon: HardDrive, title: "Google Drive", accent: "#38bdf8",
    desc: "Find a file, open it and get a summary, or save a note as a new Google Doc." },
  { icon: BellRing, title: "Reminders and tasks", accent: "#facc15",
    desc: "Reminders you set by talking, plus tasks it spots in your email so nothing slips." },
  { icon: BarChart3, title: "Data analysis", accent: "#3b82f6",
    desc: "Drop in a spreadsheet and ask for totals, rankings or a chart. The numbers come from your data, not a guess." },
  { icon: Globe, title: "Web search", accent: "#7c3aed",
    desc: "For things outside your own files, it searches the web and answers with sources." },
  { icon: Mic, title: "Voice, in Arabic and English", accent: "#f472b6",
    desc: "Speak your request and hear the answer. It replies in the language you used." },
];

/* ─── Examples for the live demo: what you say, who handles it, what happens ─── */
const demos = [
  { prompt: "Book a meeting with the team tomorrow at 5pm", agent: "Calendar agent", color: "#10b981",
    steps: ["Understood: create an event", "Resolved \"tomorrow\" in your time zone", "Waiting for your approval"],
    answer: "Team meeting, tomorrow 17:00, 60 minutes. Approve to add it to Google Calendar." },
  { prompt: "Find the budget spreadsheet in my Drive", agent: "Drive agent", color: "#38bdf8",
    steps: ["Understood: search Drive", "Looking for spreadsheets named budget", "3 files found"],
    answer: "Budget 2026 (edited Tuesday), Budget draft, Q3 budget. Open any to get a summary." },
  { prompt: "Who leads Project Falcon, and when does it launch?", agent: "Knowledge base agent", color: "#00f0ff",
    steps: ["Understood: a question about your documents", "Searching your uploads", "Answer found in falcon.pdf"],
    answer: "Dana Whitfield leads Project Falcon. The launch date is 14 March 2027." },
  { prompt: "Total sales per region in sales.csv", agent: "Data agent", color: "#3b82f6",
    steps: ["Understood: analyse a file", "Planned: sum of Sales by Region", "Computed from your data"],
    answer: "North 400, South 400. Want a bar chart?" },
  { prompt: "ذكّرني أتصل بالدكتور بكرة الساعة ٩ الصبح", agent: "Reminder agent", color: "#facc15", rtl: true,
    steps: ["فهمت: تذكير جديد", "حددت التاريخ حسب منطقتك الزمنية", "في انتظار موافقتك"],
    answer: "تذكير: الاتصال بالدكتور، غدًا الساعة 09:00. وافق لحفظه." },
];

const steps = [
  { icon: Search, title: "It understands", desc: "Your request is read for what you actually want: a question, a task, a file, a message." },
  { icon: Route, title: "It picks the right agent", desc: "Email, calendar, Drive, your documents, your data or the web. Each agent only touches what it needs." },
  { icon: UserCheck, title: "You stay in charge", desc: "Anything that changes something outside the app, like sending, booking or saving, shows you the details and waits for a yes." },
];

const trust = [
  { icon: UserCheck, title: "Nothing happens without your approval", desc: "Sending email, creating events and saving to Drive each pause on an approval card first." },
  { icon: Lock, title: "Your data stays yours", desc: "Every account has its own documents, files, reminders and Google connection. Nobody else can see them." },
  { icon: ShieldCheck, title: "Careful with what it reads", desc: "Text inside emails, web pages and files is treated as information, never as instructions it must follow." },
  { icon: Languages, title: "Speaks your language", desc: "Write or speak in Arabic or English. It answers in the same language." },
];

const navLinks = [
  { label: "Features", href: "#features" },
  { label: "How it works", href: "#how" },
  { label: "Trust", href: "#trust" },
];

/* ─── Floating particles behind the orb ─── */
function Particles() {
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      {Array.from({ length: 28 }).map((_, i) => {
        const size = Math.random() * 3 + 1;
        const x = Math.random() * 100;
        const y = Math.random() * 100;
        const delay = Math.random() * 6;
        const duration = Math.random() * 4 + 4;
        return (
          <motion.span
            key={i}
            className="absolute rounded-full"
            style={{
              width: size,
              height: size,
              left: `${x}%`,
              top: `${y}%`,
              background: i % 3 === 0 ? "#00f0ff" : i % 3 === 1 ? "#a855f7" : "#3b82f6",
            }}
            animate={{ y: [0, -30, 0], opacity: [0, 0.7, 0] }}
            transition={{ duration, delay, repeat: Infinity, ease: "easeInOut" }}
          />
        );
      })}
    </div>
  );
}

/* ─── Animated AI Orb ─── */
function AiOrb() {
  return (
    <div className="relative flex items-center justify-center w-52 h-52 sm:w-60 sm:h-60">
      <motion.div
        className="absolute inset-0 rounded-full"
        style={{ background: "radial-gradient(circle, rgba(0,240,255,0.08) 0%, transparent 70%)" }}
        animate={{ scale: [1, 1.15, 1] }}
        transition={{ duration: 5, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute rounded-full"
        style={{
          width: "105%", height: "105%", border: "1.5px solid rgba(0,240,255,0.15)",
          borderTopColor: "rgba(168,85,247,0.6)", borderRightColor: "rgba(0,240,255,0.5)",
        }}
        animate={{ rotate: 360 }}
        transition={{ duration: 8, repeat: Infinity, ease: "linear" }}
      />
      <motion.div
        className="absolute rounded-full"
        style={{
          width: "115%", height: "115%", border: "1px solid rgba(124,58,237,0.1)",
          borderBottomColor: "rgba(0,240,255,0.35)",
        }}
        animate={{ rotate: -360 }}
        transition={{ duration: 12, repeat: Infinity, ease: "linear" }}
      />
      <motion.div
        className="relative w-40 h-40 sm:w-48 sm:h-48 rounded-full"
        style={{
          background: `
            radial-gradient(circle at 35% 35%, rgba(0,240,255,0.45) 0%, transparent 55%),
            radial-gradient(circle at 65% 65%, rgba(168,85,247,0.5) 0%, transparent 55%),
            radial-gradient(circle at 50% 50%, rgba(59,130,246,0.35) 0%, rgba(10,10,10,0.9) 80%)
          `,
          boxShadow: "0 0 60px 15px rgba(0,240,255,0.18), 0 0 120px 40px rgba(124,58,237,0.10)",
        }}
        animate={{ scale: [1, 1.045, 1] }}
        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
      >
        <div
          className="absolute inset-4 rounded-full opacity-60"
          style={{ background: "radial-gradient(circle at 40% 30%, rgba(255,255,255,0.12) 0%, transparent 60%)" }}
        />
      </motion.div>
    </div>
  );
}

/* ─── Live demo: shows how a request is understood, routed and answered ─── */
function LiveDemo() {
  const [index, setIndex] = useState(0);
  const [stage, setStage] = useState(0); // 0 typing the prompt, 1..3 steps, 4 answer
  const [typed, setTyped] = useState("");
  const demo = demos[index];

  useEffect(() => {
    setTyped("");
    setStage(0);
  }, [index]);

  useEffect(() => {
    if (stage !== 0) return undefined;
    if (typed.length < demo.prompt.length) {
      const timer = setTimeout(() => setTyped(demo.prompt.slice(0, typed.length + 1)), 32);
      return () => clearTimeout(timer);
    }
    const timer = setTimeout(() => setStage(1), 450);
    return () => clearTimeout(timer);
  }, [stage, typed, demo]);

  useEffect(() => {
    if (stage < 1) return undefined;
    const delay = stage < 4 ? 650 : 3600;
    const timer = setTimeout(() => {
      if (stage < 4) setStage(stage + 1);
      else setIndex((index + 1) % demos.length);
    }, delay);
    return () => clearTimeout(timer);
  }, [stage, index]);

  return (
    <div className="relative z-10 w-full max-w-2xl mx-auto rounded-2xl border border-white/10 bg-white/[0.04] backdrop-blur-xl p-4 sm:p-5 text-left shadow-[0_0_60px_rgba(0,240,255,0.06)]">
      <div className="flex items-center gap-1.5 mb-3">
        <span className="h-2.5 w-2.5 rounded-full bg-red-400/60" />
        <span className="h-2.5 w-2.5 rounded-full bg-yellow-400/60" />
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/60" />
        <span className="ml-2 text-[11px] text-gray-500">Try asking</span>
      </div>

      <div className="min-h-[190px]" dir={demo.rtl ? "rtl" : "ltr"}>
        <div className="inline-block max-w-full rounded-xl bg-[#00f0ff]/10 border border-[#00f0ff]/20 px-4 py-2.5 text-sm text-white">
          {typed}
          {stage === 0 && <span className="ml-0.5 inline-block w-px h-4 bg-[#00f0ff] align-middle animate-pulse" />}
        </div>

        <AnimatePresence>
          {stage >= 1 && (
            <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-3">
              <span
                className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium"
                style={{ color: demo.color, borderColor: `${demo.color}55`, background: `${demo.color}14` }}
              >
                <Sparkles size={12} /> {demo.agent}
              </span>
              <ul className="list-none m-0 mt-2.5 p-0 space-y-1.5">
                {demo.steps.slice(0, Math.min(stage, 3)).map((text) => (
                  <motion.li key={text} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
                    className="flex items-center gap-2 text-xs text-gray-400">
                    <CheckCircle2 size={13} style={{ color: demo.color }} /> {text}
                  </motion.li>
                ))}
              </ul>
            </motion.div>
          )}
          {stage >= 4 && (
            <motion.p initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
              className="mt-3 mb-0 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm text-gray-200 leading-relaxed">
              {demo.answer}
            </motion.p>
          )}
        </AnimatePresence>
      </div>

      <div className="mt-3 flex items-center justify-center gap-1.5">
        {demos.map((d, i) => (
          <button key={d.prompt} aria-label={`Show example ${i + 1}`} onClick={() => setIndex(i)}
            className={`h-1.5 rounded-full border-none cursor-pointer transition-all ${i === index ? "w-6 bg-[#00f0ff]" : "w-1.5 bg-white/20 hover:bg-white/40"}`} />
        ))}
      </div>
    </div>
  );
}

/* ─── Feature Card ─── */
function FeatureCard({ icon: Icon, title, desc, accent, index }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 40 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ duration: 0.6, delay: (index % 4) * 0.1, ease: "easeOut" }}
      whileHover={{ scale: 1.03, boxShadow: `0 0 30px 4px ${accent}25`, borderColor: `${accent}40` }}
      className="group relative rounded-2xl border border-white/10 bg-white/5 backdrop-blur-xl p-6 transition-colors duration-300 cursor-default"
    >
      <div
        className="absolute top-0 left-6 right-6 h-px opacity-0 group-hover:opacity-100 transition-opacity duration-500"
        style={{ background: `linear-gradient(90deg, transparent, ${accent}, transparent)` }}
      />
      <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl" style={{ background: `${accent}12`, boxShadow: `0 0 20px 2px ${accent}10` }}>
        <Icon size={22} style={{ color: accent }} />
      </div>
      <h3 className="text-base font-semibold text-white mb-2">{title}</h3>
      <p className="text-sm leading-relaxed text-gray-400 m-0">{desc}</p>
    </motion.div>
  );
}

function SectionHeader({ badge, title, accent, text }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 30 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ duration: 0.7 }}
      className="mb-14 text-center"
    >
      <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-xs font-medium text-[#00f0ff] backdrop-blur-sm mb-4">
        <Sparkles size={12} />
        {badge}
      </span>
      <h2 className="text-3xl sm:text-4xl font-bold tracking-tight m-0">
        <span className="bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">{title}</span>{" "}
        <span className="bg-gradient-to-r from-[#00f0ff] to-[#a855f7] bg-clip-text text-transparent">{accent}</span>
      </h2>
      {text && <p className="mt-4 text-gray-500 max-w-xl mx-auto">{text}</p>}
    </motion.div>
  );
}

/* ─── Landing Page ─── */
export default function LandingPage() {
  return (
    <AnimatePresence>
      <div className="min-h-screen bg-[#0a0a0a] text-white overflow-x-hidden selection:bg-[#00f0ff]/20 selection:text-[#00f0ff]">
        {/* ============ NAV ============ */}
        <motion.nav
          initial={{ y: -30, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          transition={{ duration: 0.7, ease: "easeOut" }}
          className="fixed top-0 inset-x-0 z-50 border-b border-white/5 bg-[#0a0a0a]/70 backdrop-blur-xl"
        >
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
            <Link to="/" className="flex items-center gap-2 group">
              <Sparkles size={22} className="text-[#00f0ff] group-hover:rotate-12 transition-transform duration-300" />
              <span className="text-xl font-bold tracking-tight bg-gradient-to-r from-[#00f0ff] to-[#a855f7] bg-clip-text text-transparent">
                NexusAI
              </span>
            </Link>

            <div className="hidden md:flex items-center gap-8">
              {navLinks.map((l) => (
                <a key={l.label} href={l.href} className="text-sm text-gray-400 hover:text-white transition-colors duration-200">
                  {l.label}
                </a>
              ))}
            </div>

            <Link
              to="/auth"
              className="rounded-lg border border-white/10 bg-white/5 px-5 py-2 text-sm font-medium text-gray-300 backdrop-blur-sm hover:border-[#00f0ff]/40 hover:text-white hover:shadow-[0_0_14px_rgba(0,240,255,0.12)] transition-all duration-300"
            >
              Sign In
            </Link>
          </div>
        </motion.nav>

        {/* ============ HERO ============ */}
        <section className="relative flex flex-col items-center px-6 pt-28 pb-20">
          <div
            className="pointer-events-none absolute top-[-10%] left-1/2 -translate-x-1/2 w-[700px] h-[700px] rounded-full opacity-30"
            style={{ background: "radial-gradient(circle, rgba(0,240,255,0.12) 0%, transparent 70%)" }}
          />
          <div
            className="pointer-events-none absolute top-[10%] right-0 w-[500px] h-[500px] rounded-full opacity-20"
            style={{ background: "radial-gradient(circle, rgba(124,58,237,0.15) 0%, transparent 70%)" }}
          />
          <Particles />

          <motion.div
            initial={{ scale: 0.6, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 1.2, ease: "easeOut" }}
            className="relative z-10 mb-6"
          >
            <AiOrb />
          </motion.div>

          <motion.h1
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.3, ease: "easeOut" }}
            className="relative z-10 text-center text-4xl sm:text-5xl md:text-6xl font-extrabold leading-tight tracking-tight"
          >
            <span className="bg-gradient-to-r from-[#00f0ff] via-[#3b82f6] to-[#a855f7] bg-clip-text text-transparent">
              An AI secretary that
            </span>
            <br />
            <span className="bg-gradient-to-r from-[#a855f7] via-[#3b82f6] to-[#00f0ff] bg-clip-text text-transparent">
              understands, then acts
            </span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.5, ease: "easeOut" }}
            className="relative z-10 mt-5 max-w-2xl text-center text-base sm:text-lg text-gray-400 leading-relaxed"
          >
            Tell it what you need in plain Arabic or English. It reads your request, chooses the right tool across your
            email, calendar, Drive, documents and data, and asks before it does anything that matters.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.7, ease: "easeOut" }}
            className="relative z-10 mt-8 flex flex-wrap items-center justify-center gap-3"
          >
            <Link to="/auth">
              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.97 }}
                className="group relative inline-flex items-center gap-2.5 rounded-full bg-gradient-to-r from-[#00f0ff] to-[#3b82f6] px-8 py-3.5 text-sm font-semibold text-[#0a0a0a] shadow-[0_0_30px_rgba(0,240,255,0.35)] hover:shadow-[0_0_50px_rgba(0,240,255,0.5)] transition-shadow duration-400 cursor-pointer border-none"
              >
                <Zap size={18} className="group-hover:rotate-12 transition-transform duration-300" />
                Get Started
                <ArrowRight size={18} className="group-hover:translate-x-1 transition-transform duration-300" />
              </motion.button>
            </Link>
            <a
              href="#how"
              className="rounded-full border border-white/10 bg-white/5 px-7 py-3.5 text-sm font-medium text-gray-300 hover:border-[#00f0ff]/40 hover:text-white transition-all duration-300"
            >
              See how it works
            </a>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.9, delay: 0.9, ease: "easeOut" }}
            className="relative z-10 mt-12 w-full"
          >
            <LiveDemo />
          </motion.div>
        </section>

        {/* ============ FEATURES ============ */}
        <section id="features" className="relative px-6 py-24 sm:py-28">
          <div
            className="pointer-events-none absolute left-1/2 top-0 -translate-x-1/2 w-[600px] h-[600px] rounded-full opacity-20"
            style={{ background: "radial-gradient(circle, rgba(124,58,237,0.12) 0%, transparent 70%)" }}
          />
          <div className="relative mx-auto max-w-6xl">
            <SectionHeader
              badge="What it can do"
              title="One assistant,"
              accent="every tool you use"
              text="Each capability is its own agent. The assistant decides which one your request needs."
            />
            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
              {features.map((f, i) => (
                <FeatureCard key={f.title} {...f} index={i} />
              ))}
            </div>
          </div>
        </section>

        {/* ============ HOW IT WORKS ============ */}
        <section id="how" className="relative px-6 py-24 sm:py-28">
          <div className="mx-auto max-w-5xl">
            <SectionHeader badge="How it works" title="From a sentence" accent="to a finished task" />
            <div className="grid gap-5 md:grid-cols-3">
              {steps.map((s, i) => (
                <motion.div
                  key={s.title}
                  initial={{ opacity: 0, y: 30 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, amount: 0.3 }}
                  transition={{ duration: 0.6, delay: i * 0.15 }}
                  className="relative rounded-2xl border border-white/10 bg-white/5 p-6 backdrop-blur-xl"
                >
                  <span className="absolute -top-3 left-6 rounded-full bg-gradient-to-r from-[#00f0ff] to-[#a855f7] px-2.5 py-0.5 text-xs font-bold text-[#0a0a0a]">
                    {i + 1}
                  </span>
                  <s.icon size={26} className="mb-3 text-[#00f0ff]" />
                  <h3 className="text-base font-semibold text-white mb-2 mt-0">{s.title}</h3>
                  <p className="text-sm leading-relaxed text-gray-400 m-0">{s.desc}</p>
                </motion.div>
              ))}
            </div>
          </div>
        </section>

        {/* ============ TRUST ============ */}
        <section id="trust" className="relative px-6 py-24 sm:py-28">
          <div className="mx-auto max-w-5xl">
            <SectionHeader
              badge="Built to be trusted"
              title="Powerful, but"
              accent="never on its own"
              text="It is designed to help without taking over."
            />
            <div className="grid gap-5 sm:grid-cols-2">
              {trust.map((t, i) => (
                <motion.div
                  key={t.title}
                  initial={{ opacity: 0, y: 30 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, amount: 0.3 }}
                  transition={{ duration: 0.6, delay: (i % 2) * 0.12 }}
                  className="flex gap-4 rounded-2xl border border-white/10 bg-white/[0.03] p-6"
                >
                  <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[#00f0ff]/10">
                    <t.icon size={22} className="text-[#00f0ff]" />
                  </div>
                  <div>
                    <h3 className="text-base font-semibold text-white mt-0 mb-1.5">{t.title}</h3>
                    <p className="text-sm leading-relaxed text-gray-400 m-0">{t.desc}</p>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </section>

        {/* ============ FINAL CTA ============ */}
        <section className="px-6 pb-24">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.7 }}
            className="relative mx-auto max-w-4xl overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-[#00f0ff]/10 via-[#3b82f6]/5 to-[#a855f7]/10 p-10 sm:p-14 text-center"
          >
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight mt-0 mb-3 text-white">Hand off the busywork</h2>
            <p className="mx-auto mb-7 max-w-lg text-gray-400">
              Create an account, connect Google if you like, and ask for your first task.
            </p>
            <Link to="/auth">
              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.97 }}
                className="inline-flex items-center gap-2.5 rounded-full bg-gradient-to-r from-[#00f0ff] to-[#3b82f6] px-8 py-3.5 text-sm font-semibold text-[#0a0a0a] shadow-[0_0_30px_rgba(0,240,255,0.35)] cursor-pointer border-none"
              >
                Get Started
                <ArrowRight size={18} />
              </motion.button>
            </Link>
          </motion.div>
        </section>

        {/* ============ FOOTER ============ */}
        <footer className="border-t border-white/5 py-8 px-6">
          <div className="mx-auto max-w-7xl flex flex-col sm:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <Sparkles size={16} className="text-[#00f0ff]" />
              <span className="text-sm font-semibold bg-gradient-to-r from-[#00f0ff] to-[#a855f7] bg-clip-text text-transparent">
                NexusAI
              </span>
            </div>
            <p className="text-xs text-gray-600 m-0">
              NexusAI &copy; {new Date().getFullYear()}. An AI secretary built with multi-agent intelligence.
            </p>
          </div>
        </footer>
      </div>
    </AnimatePresence>
  );
}
