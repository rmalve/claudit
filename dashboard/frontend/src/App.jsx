import { Routes, Route } from 'react-router-dom'
import MuiTheme from './components/MuiTheme'
import Nav from './components/Nav'
import Overview from './pages/Overview'
import Findings from './pages/Findings'
import Directives from './pages/Directives'
import Escalations from './pages/Escalations'
import Sessions from './pages/Sessions'
import Evals from './pages/Evals'
import Reports from './pages/Reports'
import DataQuality from './pages/DataQuality'
import SystemHealth from './pages/SystemHealth'

function App() {
  return (
    <MuiTheme>
      <div className="min-h-screen bg-brand-bg">
        <Nav />
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/findings" element={<Findings />} />
          <Route path="/directives" element={<Directives />} />
          <Route path="/escalations" element={<Escalations />} />
          <Route path="/evals" element={<Evals />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/data-quality" element={<DataQuality />} />
          <Route path="/system" element={<SystemHealth />} />
        </Routes>
      </div>
    </MuiTheme>
  )
}

export default App
