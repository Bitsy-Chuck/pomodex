import { Outlet, Link, useNavigate } from 'react-router-dom'
import { clearTokens, isLoggedIn } from '../api/auth'

export default function Layout() {
  const navigate = useNavigate()

  function handleLogout() {
    clearTokens()
    navigate('/login')
  }

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <header style={{
        padding: '12px 24px',
        borderBottom: '1px solid #e2e8f0',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <Link to="/projects" style={{ textDecoration: 'none', color: '#1a202c', fontWeight: 700, fontSize: 20 }}>
          Pomodex
        </Link>
        {isLoggedIn() && (
          <button onClick={handleLogout} style={{
            background: 'none', border: '1px solid #cbd5e0', borderRadius: 6,
            padding: '6px 16px', cursor: 'pointer',
          }}>
            Logout
          </button>
        )}
      </header>
      <main style={{ flex: 1, padding: 24 }}>
        <Outlet />
      </main>
    </div>
  )
}
