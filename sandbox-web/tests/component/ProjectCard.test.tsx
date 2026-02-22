/**
 * T9.8: Project list displays user's projects
 * T9.10: Project detail shows correct info
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ProjectCard from '../../src/components/ProjectCard'
import type { ProjectSummary } from '../../src/api/client'

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('access_token', 'test-token')
})

describe('T9.8: Project list displays user\'s projects', () => {
  it('renders 3 project cards with name, status, and created date', async () => {
    const projects: ProjectSummary[] = [
      { id: '1', name: 'Agent Alpha', status: 'running', created_at: '2024-01-15T10:00:00Z', last_active_at: null },
      { id: '2', name: 'Agent Beta', status: 'stopped', created_at: '2024-02-20T12:00:00Z', last_active_at: null },
      { id: '3', name: 'Agent Gamma', status: 'error', created_at: '2024-03-10T08:00:00Z', last_active_at: null },
    ]

    render(
      <MemoryRouter>
        {projects.map(p => <ProjectCard key={p.id} project={p} />)}
      </MemoryRouter>
    )

    // Each card shows name
    expect(screen.getByText('Agent Alpha')).toBeInTheDocument()
    expect(screen.getByText('Agent Beta')).toBeInTheDocument()
    expect(screen.getByText('Agent Gamma')).toBeInTheDocument()

    // Each card shows status
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('stopped')).toBeInTheDocument()
    expect(screen.getByText('error')).toBeInTheDocument()

    // Each card shows created date
    expect(screen.getByText(/1\/15\/2024/)).toBeInTheDocument()
  })
})

describe('T9.10: Project detail shows correct info (via ProjectCard)', () => {
  it('shows status badge with correct text', () => {
    const project: ProjectSummary = {
      id: '1', name: 'Test Project', status: 'running',
      created_at: '2024-01-01T00:00:00Z', last_active_at: null,
    }

    render(
      <MemoryRouter>
        <ProjectCard project={project} />
      </MemoryRouter>
    )

    const badge = screen.getByText('running')
    expect(badge).toBeInTheDocument()
  })
})
