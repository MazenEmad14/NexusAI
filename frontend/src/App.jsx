import { Routes, Route } from 'react-router-dom'
import { AnimatePresence } from 'framer-motion'
import LandingPage from './pages/LandingPage'
import AuthPage from './pages/AuthPage'
import WorkspacePage from './pages/WorkspacePage'

function App() {
  return (
    <AnimatePresence mode="wait">
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/auth" element={<AuthPage />} />
        <Route path="/workspace" element={<WorkspacePage />} />
      </Routes>
    </AnimatePresence>
  )
}

export default App
