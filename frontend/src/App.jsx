import { Routes, Route } from 'react-router-dom'
import { AnimatePresence } from 'framer-motion'
import { AuthProvider, RequireAuth } from './hooks/useAuth'
import LandingPage from './pages/LandingPage'
import AuthPage from './pages/AuthPage'
import WorkspacePage from './pages/WorkspacePage'

function App() {
  return (
    <AuthProvider>
      <AnimatePresence mode="wait">
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/auth" element={<AuthPage />} />
          <Route path="/workspace" element={<RequireAuth><WorkspacePage /></RequireAuth>} />
        </Routes>
      </AnimatePresence>
    </AuthProvider>
  )
}

export default App
