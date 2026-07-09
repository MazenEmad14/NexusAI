import { motion, AnimatePresence } from "framer-motion";
import { Brain, Mail, Globe, Mic, ArrowRight, Sparkles, Zap } from "lucide-react";
import { Link } from "react-router-dom";

const features = [
  {
    icon: Brain,
    title: "RAG Knowledge Base",
    desc: "Upload documents and let AI agents search through your personal knowledge base for instant, accurate answers.",
    accent: "#00f0ff",
  },
  {
    icon: Mail,
    title: "Gmail Automation",
    desc: "Read, draft, and send emails autonomously. Your AI secretary manages your inbox intelligently.",
    accent: "#a855f7",
  },
  {
    icon: Globe,
    title: "Web Search",
    desc: "Real-time web search capabilities powered by intelligent agents that find and synthesize information.",
    accent: "#3b82f6",
  },
  {
    icon: Mic,
    title: "Voice Interaction",
    desc: "Speak naturally to your AI workspace. Voice commands and responses for a hands-free experience.",
    accent: "#7c3aed",
  },
];

const navLinks = ["Features", "About", "Pricing"];

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
              background:
                i % 3 === 0
                  ? "#00f0ff"
                  : i % 3 === 1
                  ? "#a855f7"
                  : "#3b82f6",
            }}
            animate={{
              y: [0, -30, 0],
              opacity: [0, 0.7, 0],
            }}
            transition={{
              duration,
              delay,
              repeat: Infinity,
              ease: "easeInOut",
            }}
          />
        );
      })}
    </div>
  );
}

/* ─── Animated AI Orb ─── */
function AiOrb() {
  return (
    <div className="relative flex items-center justify-center w-64 h-64 sm:w-72 sm:h-72 md:w-80 md:h-80">
      {/* Outermost soft glow */}
      <motion.div
        className="absolute inset-0 rounded-full"
        style={{
          background:
            "radial-gradient(circle, rgba(0,240,255,0.08) 0%, transparent 70%)",
        }}
        animate={{ scale: [1, 1.15, 1] }}
        transition={{ duration: 5, repeat: Infinity, ease: "easeInOut" }}
      />

      {/* Rotating ring */}
      <motion.div
        className="absolute rounded-full"
        style={{
          width: "105%",
          height: "105%",
          border: "1.5px solid rgba(0,240,255,0.15)",
          borderTopColor: "rgba(168,85,247,0.6)",
          borderRightColor: "rgba(0,240,255,0.5)",
        }}
        animate={{ rotate: 360 }}
        transition={{ duration: 8, repeat: Infinity, ease: "linear" }}
      />

      {/* Second counter-rotating ring */}
      <motion.div
        className="absolute rounded-full"
        style={{
          width: "115%",
          height: "115%",
          border: "1px solid rgba(124,58,237,0.1)",
          borderBottomColor: "rgba(0,240,255,0.35)",
        }}
        animate={{ rotate: -360 }}
        transition={{ duration: 12, repeat: Infinity, ease: "linear" }}
      />

      {/* Core orb — layered radial gradients */}
      <motion.div
        className="relative w-48 h-48 sm:w-56 sm:h-56 md:w-60 md:h-60 rounded-full"
        style={{
          background: `
            radial-gradient(circle at 35% 35%, rgba(0,240,255,0.45) 0%, transparent 55%),
            radial-gradient(circle at 65% 65%, rgba(168,85,247,0.5) 0%, transparent 55%),
            radial-gradient(circle at 50% 50%, rgba(59,130,246,0.35) 0%, rgba(10,10,10,0.9) 80%)
          `,
          boxShadow:
            "0 0 60px 15px rgba(0,240,255,0.18), 0 0 120px 40px rgba(124,58,237,0.10)",
        }}
        animate={{
          scale: [1, 1.045, 1],
          boxShadow: [
            "0 0 60px 15px rgba(0,240,255,0.18), 0 0 120px 40px rgba(124,58,237,0.10)",
            "0 0 80px 25px rgba(0,240,255,0.28), 0 0 140px 50px rgba(124,58,237,0.18)",
            "0 0 60px 15px rgba(0,240,255,0.18), 0 0 120px 40px rgba(124,58,237,0.10)",
          ],
        }}
        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
      >
        {/* Inner highlight */}
        <div
          className="absolute inset-4 rounded-full opacity-60"
          style={{
            background:
              "radial-gradient(circle at 40% 30%, rgba(255,255,255,0.12) 0%, transparent 60%)",
          }}
        />
      </motion.div>
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
      transition={{ duration: 0.6, delay: index * 0.12, ease: "easeOut" }}
      whileHover={{
        scale: 1.04,
        boxShadow: `0 0 30px 4px ${accent}25`,
        borderColor: `${accent}40`,
      }}
      className="group relative rounded-2xl border border-white/10 bg-white/5 backdrop-blur-xl p-6 sm:p-8 transition-colors duration-300 cursor-default"
    >
      {/* Accent top-line */}
      <div
        className="absolute top-0 left-6 right-6 h-px opacity-0 group-hover:opacity-100 transition-opacity duration-500"
        style={{ background: `linear-gradient(90deg, transparent, ${accent}, transparent)` }}
      />

      <div
        className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl"
        style={{
          background: `${accent}12`,
          boxShadow: `0 0 20px 2px ${accent}10`,
        }}
      >
        <Icon size={24} style={{ color: accent }} />
      </div>

      <h3 className="text-lg font-semibold text-white mb-2">{title}</h3>
      <p className="text-sm leading-relaxed text-gray-400">{desc}</p>
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
            {/* Logo */}
            <Link to="/" className="flex items-center gap-2 group">
              <Sparkles
                size={22}
                className="text-[#00f0ff] group-hover:rotate-12 transition-transform duration-300"
              />
              <span className="text-xl font-bold tracking-tight bg-gradient-to-r from-[#00f0ff] to-[#a855f7] bg-clip-text text-transparent">
                NexusAI
              </span>
            </Link>

            {/* Links */}
            <div className="hidden md:flex items-center gap-8">
              {navLinks.map((l) => (
                <a
                  key={l}
                  href={`#${l.toLowerCase()}`}
                  className="text-sm text-gray-400 hover:text-white transition-colors duration-200"
                >
                  {l}
                </a>
              ))}
            </div>

            {/* Sign In */}
            <Link
              to="/auth"
              className="rounded-lg border border-white/10 bg-white/5 px-5 py-2 text-sm font-medium text-gray-300 backdrop-blur-sm hover:border-[#00f0ff]/40 hover:text-white hover:shadow-[0_0_14px_rgba(0,240,255,0.12)] transition-all duration-300"
            >
              Sign In
            </Link>
          </div>
        </motion.nav>

        {/* ============ HERO ============ */}
        <section className="relative flex flex-col items-center justify-center min-h-screen px-6 pt-24">
          {/* Ambient background glows */}
          <div
            className="pointer-events-none absolute top-[-10%] left-1/2 -translate-x-1/2 w-[700px] h-[700px] rounded-full opacity-30"
            style={{
              background:
                "radial-gradient(circle, rgba(0,240,255,0.12) 0%, transparent 70%)",
            }}
          />
          <div
            className="pointer-events-none absolute top-[10%] right-0 w-[500px] h-[500px] rounded-full opacity-20"
            style={{
              background:
                "radial-gradient(circle, rgba(124,58,237,0.15) 0%, transparent 70%)",
            }}
          />

          <Particles />

          {/* Orb */}
          <motion.div
            initial={{ scale: 0.6, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 1.2, ease: "easeOut" }}
            className="relative z-10 mb-10"
          >
            <AiOrb />
          </motion.div>

          {/* Headline */}
          <motion.h1
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.4, ease: "easeOut" }}
            className="relative z-10 text-center text-4xl sm:text-5xl md:text-6xl lg:text-7xl font-extrabold leading-tight tracking-tight"
          >
            <span className="bg-gradient-to-r from-[#00f0ff] via-[#3b82f6] to-[#a855f7] bg-clip-text text-transparent">
              Your Autonomous
            </span>
            <br />
            <span className="bg-gradient-to-r from-[#a855f7] via-[#3b82f6] to-[#00f0ff] bg-clip-text text-transparent">
              AI Secretary
            </span>
          </motion.h1>

          {/* Sub-headline */}
          <motion.p
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.7, ease: "easeOut" }}
            className="relative z-10 mt-6 max-w-2xl text-center text-base sm:text-lg text-gray-400 leading-relaxed"
          >
            Powered by multi-agent intelligence — RAG Knowledge Base, Gmail
            Automation, Web Search, and Voice interaction — all working together
            seamlessly.
          </motion.p>

          {/* CTA */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 1, ease: "easeOut" }}
            className="relative z-10 mt-10"
          >
            <Link to="/auth">
              <motion.button
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.97 }}
                className="group relative inline-flex items-center gap-2.5 rounded-full bg-gradient-to-r from-[#00f0ff] to-[#3b82f6] px-8 py-3.5 text-sm font-semibold text-[#0a0a0a] shadow-[0_0_30px_rgba(0,240,255,0.35)] hover:shadow-[0_0_50px_rgba(0,240,255,0.5)] transition-shadow duration-400 cursor-pointer"
              >
                <Zap size={18} className="group-hover:rotate-12 transition-transform duration-300" />
                Get Started
                <ArrowRight
                  size={18}
                  className="group-hover:translate-x-1 transition-transform duration-300"
                />
              </motion.button>
            </Link>
          </motion.div>

          {/* Scroll indicator */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 2, duration: 1 }}
            className="absolute bottom-10 z-10 flex flex-col items-center gap-2"
          >
            <span className="text-xs text-gray-600 tracking-widest uppercase">
              Scroll
            </span>
            <motion.div
              className="w-px h-8 bg-gradient-to-b from-[#00f0ff]/50 to-transparent"
              animate={{ scaleY: [1, 1.5, 1], opacity: [0.5, 1, 0.5] }}
              transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
            />
          </motion.div>
        </section>

        {/* ============ FEATURES ============ */}
        <section id="features" className="relative px-6 py-28 sm:py-36">
          {/* Section glow */}
          <div
            className="pointer-events-none absolute left-1/2 top-0 -translate-x-1/2 w-[600px] h-[600px] rounded-full opacity-20"
            style={{
              background:
                "radial-gradient(circle, rgba(124,58,237,0.12) 0%, transparent 70%)",
            }}
          />

          <div className="mx-auto max-w-6xl">
            {/* Section header */}
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.3 }}
              transition={{ duration: 0.7 }}
              className="mb-16 text-center"
            >
              <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-4 py-1.5 text-xs font-medium text-[#00f0ff] backdrop-blur-sm mb-4">
                <Sparkles size={12} />
                Core Capabilities
              </span>
              <h2 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight">
                <span className="bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">
                  Intelligent Agents,
                </span>{" "}
                <span className="bg-gradient-to-r from-[#00f0ff] to-[#a855f7] bg-clip-text text-transparent">
                  Infinite Possibilities
                </span>
              </h2>
              <p className="mt-4 text-gray-500 max-w-xl mx-auto">
                Each agent is purpose-built, working in harmony to create a
                seamless autonomous experience.
              </p>
            </motion.div>

            {/* Cards grid */}
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              {features.map((f, i) => (
                <FeatureCard key={f.title} {...f} index={i} />
              ))}
            </div>
          </div>
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
            <p className="text-xs text-gray-600">
              NexusAI &copy; 2025. Built with Multi-Agent Intelligence.
            </p>
          </div>
        </footer>
      </div>
    </AnimatePresence>
  );
}
