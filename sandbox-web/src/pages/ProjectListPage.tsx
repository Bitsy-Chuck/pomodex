import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { listProjects, createProject, type ProjectSummary } from '../api/client'
import ProjectCard from '../components/ProjectCard'

export default function ProjectListPage() {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function handleCreate(e: FormEvent) {
    e.preventDefault()
    setCreating(true)
    try {
      const project = await createProject(newName)
      navigate(`/projects/${project.id}`, { state: { sshKey: project.ssh_private_key } })
    } catch {
      setCreating(false)
    }
  }

  if (loading) return <p>Loading projects...</p>

  return (
    <div style={{ maxWidth: 700, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1 style={{ margin: 0 }}>Projects</h1>
        <button onClick={() => setShowCreate(true)} style={{
          background: '#3182ce', color: '#fff', border: 'none',
          borderRadius: 6, padding: '8px 20px', cursor: 'pointer',
        }}>
          New Project
        </button>
      </div>

      {showCreate && (
        <form onSubmit={handleCreate} style={{
          marginBottom: 24, padding: 16, border: '1px solid #e2e8f0', borderRadius: 8,
        }}>
          <input
            autoFocus placeholder="Project name" required value={newName}
            onChange={e => setNewName(e.target.value)}
            style={{ padding: 8, marginRight: 8, width: 300 }}
          />
          <button type="submit" disabled={creating} style={{
            background: '#38a169', color: '#fff', border: 'none',
            borderRadius: 6, padding: '8px 16px', cursor: 'pointer',
          }}>
            {creating ? 'Creating...' : 'Create'}
          </button>
        </form>
      )}

      {projects.length === 0 ? (
        <p>No projects yet. Create one to get started.</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {projects.map(p => <ProjectCard key={p.id} project={p} />)}
        </div>
      )}
    </div>
  )
}
