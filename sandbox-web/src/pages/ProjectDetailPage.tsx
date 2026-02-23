import { useEffect, useState } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import {
  getProject, stopProject, startProject, deleteProject, listSnapshots,
  type ProjectDetail, type SnapshotItem,
} from '../api/client'
import { getAccessToken } from '../api/auth'
import Terminal from '../components/Terminal'

const STATUS_COLORS: Record<string, string> = {
  running: '#38a169',
  stopped: '#a0aec0',
  error: '#e53e3e',
  snapshotting: '#d69e2e',
  restoring: '#3182ce',
}

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const [project, setProject] = useState<ProjectDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [showKey, setShowKey] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState('')
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [snapshots, setSnapshots] = useState<SnapshotItem[]>([])
  const [snapshotsLoading, setSnapshotsLoading] = useState(false)

  // Show SSH key from create flow (show-once UX)
  const sshKeyFromCreate = (location.state as { sshKey?: string } | null)?.sshKey

  useEffect(() => {
    if (!id) return
    getProject(id)
      .then(setProject)
      .catch(() => {})
      .finally(() => setLoading(false))
    fetchSnapshots()
  }, [id])

  function fetchSnapshots() {
    if (!id) return
    setSnapshotsLoading(true)
    listSnapshots(id)
      .then(setSnapshots)
      .catch(() => setSnapshots([]))
      .finally(() => setSnapshotsLoading(false))
  }

  async function handlePause() {
    if (!id) return
    setActionLoading('pause')
    try {
      const updated = await stopProject(id)
      setProject(updated)
      fetchSnapshots()
    } catch {
      // error handled by UI state
    } finally {
      setActionLoading(null)
    }
  }

  async function handleStart(snapshotTag?: string) {
    if (!id) return
    setActionLoading(snapshotTag ? `restore-${snapshotTag}` : 'start')
    try {
      const updated = await startProject(id, snapshotTag)
      setProject(updated)
    } catch {
      // error handled by UI state
    } finally {
      setActionLoading(null)
    }
  }

  async function handleDelete() {
    if (!id || !project) return
    if (confirmDelete !== project.name) return
    setActionLoading('delete')
    try {
      await deleteProject(id)
      navigate('/projects')
    } catch {
      setActionLoading(null)
    }
  }

  if (loading) return <p>Loading...</p>
  if (!project) return <p>Project not found.</p>

  const color = STATUS_COLORS[project.status] || '#a0aec0'
  const isTransitioning = ['snapshotting', 'restoring', 'creating'].includes(project.status)

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 style={{ margin: 0 }}>{project.name}</h1>
        <span style={{
          background: color, color: '#fff', borderRadius: 12,
          padding: '4px 14px', fontSize: 14,
        }}>
          {project.status}
        </span>
      </div>

      {sshKeyFromCreate && (
        <div style={{
          background: '#fefcbf', border: '1px solid #d69e2e', borderRadius: 8,
          padding: 16, marginBottom: 24,
        }}>
          <strong>Save your SSH private key now — it won't be shown again.</strong>
          <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', marginTop: 8 }}>
            {sshKeyFromCreate}
          </pre>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24 }}>
        <div>
          <h3>SSH Access</h3>
          {project.ssh_host ? (
            <>
              <code>ssh {project.ssh_user ?? 'agent'}@{project.ssh_host} -p {project.ssh_port} -i key_file</code>
              <div style={{ marginTop: 8 }}>
                <button onClick={() => setShowKey(!showKey)} style={{
                  background: 'none', border: '1px solid #cbd5e0', borderRadius: 4,
                  padding: '4px 10px', cursor: 'pointer', fontSize: 12,
                }}>
                  {showKey ? 'Hide' : 'Show'} private key
                </button>
                {showKey && project.ssh_private_key && (
                  <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', marginTop: 8 }}>{project.ssh_private_key}</pre>
                )}
              </div>
            </>
          ) : (
            <p style={{ color: '#718096' }}>Not available (project is {project.status})</p>
          )}
        </div>
        <div>
          <h3>Backup Info</h3>
          <p>Last backup: {project.last_backup_at ? new Date(project.last_backup_at).toLocaleString() : 'Never'}</p>
        </div>
      </div>

      <div style={{ marginBottom: 24, display: 'flex', gap: 8 }}>
        {project.status === 'running' && (
          <button
            onClick={handlePause}
            disabled={actionLoading !== null}
            style={{
              background: '#d69e2e', color: '#fff', border: 'none',
              borderRadius: 6, padding: '8px 16px', cursor: actionLoading ? 'not-allowed' : 'pointer',
              opacity: actionLoading ? 0.6 : 1,
            }}
          >
            {actionLoading === 'pause' ? 'Pausing...' : 'Pause'}
          </button>
        )}
        {project.status === 'stopped' && (
          <button
            onClick={() => handleStart()}
            disabled={actionLoading !== null}
            style={{
              background: '#38a169', color: '#fff', border: 'none',
              borderRadius: 6, padding: '8px 16px', cursor: actionLoading ? 'not-allowed' : 'pointer',
              opacity: actionLoading ? 0.6 : 1,
            }}
          >
            {actionLoading === 'start' ? 'Starting...' : 'Start'}
          </button>
        )}
        {!isTransitioning && (
          <button onClick={() => setConfirmDelete(prev => prev === '' ? ' ' : '')} style={{
            background: '#718096', color: '#fff', border: 'none',
            borderRadius: 6, padding: '8px 16px', cursor: 'pointer',
          }}>
            Delete
          </button>
        )}
      </div>

      {confirmDelete !== '' && (
        <div style={{
          border: '1px solid #e53e3e', borderRadius: 8, padding: 16, marginBottom: 24,
        }}>
          <p>Type <strong>{project.name}</strong> to confirm deletion:</p>
          <input
            value={confirmDelete.trim() ? confirmDelete : ''}
            onChange={e => setConfirmDelete(e.target.value)}
            placeholder={project.name}
            style={{ padding: 8, marginRight: 8, width: 250 }}
          />
          <button
            onClick={handleDelete}
            disabled={confirmDelete !== project.name || actionLoading === 'delete'}
            style={{
              background: confirmDelete === project.name ? '#e53e3e' : '#cbd5e0',
              color: '#fff', border: 'none', borderRadius: 6,
              padding: '8px 16px', cursor: 'pointer',
            }}
          >
            {actionLoading === 'delete' ? 'Deleting...' : 'Confirm Delete'}
          </button>
        </div>
      )}

      {/* Snapshots table */}
      <div style={{ marginBottom: 24 }}>
        <h3>Snapshots</h3>
        {snapshotsLoading ? (
          <p style={{ color: '#718096' }}>Loading snapshots...</p>
        ) : snapshots.length === 0 ? (
          <p style={{ color: '#718096' }}>No snapshots yet. Pause the project to create one.</p>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '2px solid #e2e8f0', textAlign: 'left' }}>
                <th style={{ padding: '8px 12px' }}>Tag</th>
                <th style={{ padding: '8px 12px' }}>Created</th>
                <th style={{ padding: '8px 12px' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {snapshots.map((snap) => (
                <tr key={snap.tag} style={{ borderBottom: '1px solid #e2e8f0' }}>
                  <td style={{ padding: '8px 12px', fontFamily: 'monospace', fontSize: 13 }}>{snap.tag}</td>
                  <td style={{ padding: '8px 12px' }}>{new Date(snap.created_at).toLocaleString()}</td>
                  <td style={{ padding: '8px 12px' }}>
                    <button
                      onClick={() => handleStart(snap.tag)}
                      disabled={project.status !== 'stopped' || actionLoading !== null}
                      style={{
                        background: project.status === 'stopped' && !actionLoading ? '#3182ce' : '#cbd5e0',
                        color: '#fff', border: 'none', borderRadius: 4,
                        padding: '4px 12px', cursor: project.status === 'stopped' && !actionLoading ? 'pointer' : 'not-allowed',
                        fontSize: 13,
                      }}
                    >
                      {actionLoading === `restore-${snap.tag}` ? 'Restoring...' : 'Restore'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {project.status === 'running' && project.terminal_url && (() => {
        const token = getAccessToken()
        const fullUrl = `${project.terminal_url}?token=${token}`
        return (
          <div>
            <h3>Terminal</h3>
            <Terminal wsUrl={fullUrl} />
          </div>
        )
      })()}
    </div>
  )
}
