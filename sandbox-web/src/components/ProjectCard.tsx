import { Link } from 'react-router-dom'
import type { ProjectSummary } from '../api/client'

const STATUS_COLORS: Record<string, string> = {
  running: '#38a169',
  stopped: '#a0aec0',
  error: '#e53e3e',
  snapshotting: '#d69e2e',
}

export default function ProjectCard({ project }: { project: ProjectSummary }) {
  const color = STATUS_COLORS[project.status] || '#a0aec0'
  return (
    <Link
      to={`/projects/${project.id}`}
      style={{
        display: 'block', textDecoration: 'none', color: 'inherit',
        border: '1px solid #e2e8f0', borderRadius: 8, padding: 16,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <strong>{project.name}</strong>
        <span style={{
          background: color, color: '#fff', borderRadius: 12,
          padding: '2px 10px', fontSize: 12,
        }}>
          {project.status}
        </span>
      </div>
      <div style={{ color: '#718096', fontSize: 13, marginTop: 8 }}>
        Created {new Date(project.created_at).toLocaleDateString()}
      </div>
    </Link>
  )
}
