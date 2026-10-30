import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Mail, Lock, User, ArrowLeft, Eye, EyeOff } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { signIn, signUp, getGoogleAuthUrl } from "../services/api";

const GoogleIcon = () => (
// ... keep existing code ...
  <svg viewBox="0 0 24 24" width="20" height="20" className="shrink-0">
    <path
      d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
      fill="#4285F4"
    />
    <path
      d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
      fill="#34A853"
    />
    <path
      d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
      fill="#FBBC05"
    />
    <path
      d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
      fill="#EA4335"
    />
  </svg>
);

const InputField = ({ icon: Icon, type: initialType, placeholder, value, onChange, isPassword }) => {
  const [showPassword, setShowPassword] = useState(false);
  const type = isPassword ? (showPassword ? "text" : "password") : initialType;

  return (
    <div className="relative group">
      <div className="absolute left-3.5 top-1/2 -translate-y-1/2 text-white/30 group-focus-within:text-[#00f0ff] transition-colors duration-300">
        <Icon size={18} />
      </div>
      <input
        type={type}
        placeholder={placeholder}
        value={value}
        onChange={onChange}
        required
        className="w-full bg-white/5 border border-white/10 rounded-xl py-3 pl-11 pr-11 text-white placeholder-white/30 text-sm outline-none focus:border-[#00f0ff] focus:ring-2 focus:ring-[#00f0ff]/20 transition-all duration-300"
      />
      {isPassword && (
        <button
          type="button"
          onClick={() => setShowPassword(!showPassword)}
          className="absolute right-3.5 top-1/2 -translate-y-1/2 text-white/30 hover:text-white/60 transition-colors duration-300"
        >
          {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
        </button>
      )}
    </div>
  );
};

const cardVariants = {
  hidden: { opacity: 0, y: 30, scale: 0.96 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.5, ease: [0.22, 1, 0.36, 1] },
  },
};

const formVariants = {
  enter: { opacity: 0, x: 20 },
  center: { opacity: 1, x: 0, transition: { duration: 0.35, ease: "easeOut" } },
  exit: { opacity: 0, x: -20, transition: { duration: 0.25, ease: "easeIn" } },
};

export default function AuthPage() {
  const [mode, setMode] = useState("signin");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const [signInData, setSignInData] = useState({ email: "", password: "" });
  const [signUpData, setSignUpData] = useState({
    name: "",
    email: "",
    password: "",
    confirmPassword: "",
  });

  const handleSignIn = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await signIn(signInData.email, signInData.password);
      if (res && res.access_token) {
        localStorage.setItem("jwt_token", res.access_token);
        
        if (res.user && !res.user.is_google_connected) {
           const googleRes = await getGoogleAuthUrl();
           if (googleRes && googleRes.auth_url) {
             window.location.href = googleRes.auth_url;
             return;
           }
        }
        navigate("/workspace");
      }
    } catch (err) {
      setError(err?.message || "Sign in failed");
    } finally {
      setLoading(false);
    }
  };

  const handleSignUp = async (e) => {
    e.preventDefault();
    if (signUpData.password !== signUpData.confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const res = await signUp(signUpData.name, signUpData.email, signUpData.password);
      if (res && res.access_token) {
        localStorage.setItem("jwt_token", res.access_token);
        
        const googleRes = await getGoogleAuthUrl();
        if (googleRes && googleRes.auth_url) {
          window.location.href = googleRes.auth_url;
          return;
        }
        navigate("/workspace");
      }
    } catch (err) {
      setError(err?.message || "Sign up failed");
    } finally {
      setLoading(false);
    }
  };

  const handleGoogle = async (e) => {
    e.preventDefault();
    try {
        const data = await getGoogleAuthUrl();
        if (data && data.auth_url) {
            window.location.href = data.auth_url;
        }
    } catch (err) {
        setError("Failed to load Google Auth URL");
    }
  };

  return (
    <div className="relative min-h-screen bg-[#0a0a0a] flex items-center justify-center overflow-hidden px-4">
      {/* ── Animated Background Blobs ── */}
      <motion.div
        animate={{ x: [0, 40, -30, 0], y: [0, -50, 30, 0], scale: [1, 1.15, 0.95, 1] }}
        transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
        className="pointer-events-none absolute -top-32 -left-32 h-[480px] w-[480px] rounded-full bg-[#00f0ff]/8 blur-[120px]"
      />
      <motion.div
        animate={{ x: [0, -50, 40, 0], y: [0, 40, -40, 0], scale: [1, 0.9, 1.1, 1] }}
        transition={{ duration: 22, repeat: Infinity, ease: "easeInOut" }}
        className="pointer-events-none absolute -bottom-40 -right-40 h-[520px] w-[520px] rounded-full bg-[#a855f7]/8 blur-[120px]"
      />
      <motion.div
        animate={{ x: [0, 30, -20, 0], y: [0, -30, 20, 0] }}
        transition={{ duration: 15, repeat: Infinity, ease: "easeInOut" }}
        className="pointer-events-none absolute top-1/3 left-1/2 -translate-x-1/2 h-[350px] w-[350px] rounded-full bg-[#6366f1]/6 blur-[100px]"
      />

      {/* ── Subtle Grid Overlay ── */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.06) 1px, transparent 1px)",
          backgroundSize: "60px 60px",
        }}
      />

      {/* ── Auth Card ── */}
      <motion.div
        variants={cardVariants}
        initial="hidden"
        animate="visible"
        className="relative z-10 w-full max-w-md"
      >
        <div className="bg-white/5 backdrop-blur-xl border border-white/10 rounded-2xl p-8 shadow-2xl shadow-black/40">
          {/* ── Header ── */}
          <div className="text-center mb-8">
            <Link to="/" className="inline-flex items-center gap-2 mb-6 text-white/40 hover:text-[#00f0ff] transition-colors text-sm group">
              <ArrowLeft size={16} className="group-hover:-translate-x-0.5 transition-transform" />
              Back to home
            </Link>
            <h1 className="text-2xl font-bold text-white tracking-tight">
              {mode === "signin" ? "Welcome Back" : "Create Account"}
            </h1>
            <p className="text-white/40 text-sm mt-1.5">
              {mode === "signin"
                ? "Sign in to your AI workspace"
                : "Get started with your AI workspace"}
            </p>
          </div>

          {/* ── Mode Tabs ── */}
          <div className="relative flex bg-white/5 rounded-xl p-1 mb-7">
            <motion.div
              layout
              className="absolute inset-y-1 rounded-lg bg-gradient-to-r from-[#00f0ff]/15 to-[#a855f7]/15 border border-white/10"
              style={{ width: "calc(50% - 4px)" }}
              animate={{ x: mode === "signin" ? 4 : "calc(100% + 4px)" }}
              transition={{ type: "spring", stiffness: 350, damping: 30 }}
            />
            {["signin", "signup"].map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`relative z-10 flex-1 py-2.5 text-sm font-medium rounded-lg transition-colors duration-300 cursor-pointer ${
                  mode === m ? "text-white" : "text-white/40 hover:text-white/60"
                }`}
              >
                {m === "signin" ? "Sign In" : "Sign Up"}
              </button>
            ))}
          </div>

          {/* ── Google Button ── */}
          <motion.button
            whileHover={{ scale: 1.01 }}
            whileTap={{ scale: 0.98 }}
            onClick={handleGoogle}
            className="group relative w-full flex items-center justify-center gap-3 py-3 px-4 bg-white/10 hover:bg-white/[0.14] border border-white/10 hover:border-white/20 rounded-xl text-white font-medium text-sm transition-all duration-300 cursor-pointer hover:shadow-[0_0_24px_rgba(255,255,255,0.06)]"
          >
            <GoogleIcon />
            <span>Continue with Google</span>
          </motion.button>

          {/* ── Divider ── */}
          <div className="flex items-center gap-4 my-6">
            <div className="flex-1 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
            <span className="text-white/25 text-xs font-medium uppercase tracking-widest">or</span>
            <div className="flex-1 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent" />
          </div>

          {error && (
            <div className="mb-6 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm text-center">
              {error}
            </div>
          )}

          {/* ── Forms ── */}
          <AnimatePresence mode="wait">
            {mode === "signin" ? (
              <motion.form
                key="signin"
                variants={formVariants}
                initial="enter"
                animate="center"
                exit="exit"
                onSubmit={handleSignIn}
                className="space-y-4"
              >
                <InputField
                  icon={Mail}
                  type="email"
                  placeholder="Email address"
                  value={signInData.email}
                  onChange={(e) => setSignInData({ ...signInData, email: e.target.value })}
                />
                <InputField
                  icon={Lock}
                  type="password"
                  placeholder="Password"
                  value={signInData.password}
                  onChange={(e) => setSignInData({ ...signInData, password: e.target.value })}
                  isPassword
                />

                <div className="flex justify-end">
                  <button
                    type="button"
                    className="text-xs text-[#00f0ff]/70 hover:text-[#00f0ff] transition-colors cursor-pointer"
                  >
                    Forgot Password?
                  </button>
                </div>

                <motion.button
                  type="submit"
                  whileHover={{ scale: 1.01 }}
                  whileTap={{ scale: 0.97 }}
                  className="w-full py-3 rounded-xl bg-gradient-to-r from-[#00f0ff] to-[#a855f7] text-white font-semibold text-sm shadow-[0_0_30px_rgba(0,240,255,0.25)] hover:shadow-[0_0_40px_rgba(0,240,255,0.4)] transition-shadow duration-300 cursor-pointer"
                >
                  Sign In
                </motion.button>
              </motion.form>
            ) : (
              <motion.form
                key="signup"
                variants={formVariants}
                initial="enter"
                animate="center"
                exit="exit"
                onSubmit={handleSignUp}
                className="space-y-4"
              >
                <InputField
                  icon={User}
                  type="text"
                  placeholder="Full Name"
                  value={signUpData.name}
                  onChange={(e) => setSignUpData({ ...signUpData, name: e.target.value })}
                />
                <InputField
                  icon={Mail}
                  type="email"
                  placeholder="Email address"
                  value={signUpData.email}
                  onChange={(e) => setSignUpData({ ...signUpData, email: e.target.value })}
                />
                <InputField
                  icon={Lock}
                  type="password"
                  placeholder="Password"
                  value={signUpData.password}
                  onChange={(e) => setSignUpData({ ...signUpData, password: e.target.value })}
                  isPassword
                />
                <InputField
                  icon={Lock}
                  type="password"
                  placeholder="Confirm Password"
                  value={signUpData.confirmPassword}
                  onChange={(e) =>
                    setSignUpData({ ...signUpData, confirmPassword: e.target.value })
                  }
                  isPassword
                />

                <motion.button
                  type="submit"
                  whileHover={{ scale: 1.01 }}
                  whileTap={{ scale: 0.97 }}
                  className="w-full py-3 rounded-xl bg-gradient-to-r from-[#00f0ff] to-[#a855f7] text-white font-semibold text-sm shadow-[0_0_30px_rgba(0,240,255,0.25)] hover:shadow-[0_0_40px_rgba(0,240,255,0.4)] transition-shadow duration-300 cursor-pointer"
                >
                  Create Account
                </motion.button>
              </motion.form>
            )}
          </AnimatePresence>
        </div>

        {/* ── Footer Link ── */}
        <div className="text-center mt-6">
          <Link
            to="/"
            className="text-white/30 hover:text-white/50 text-xs transition-colors"
          >
            ← Return to Landing Page
          </Link>
        </div>
      </motion.div>
    </div>
  );
}
