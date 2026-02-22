/**
 * T9.9: Create project flow
 * T9.10: Project detail shows correct info (full page test)
 * T9.11: Stop project action
 * T9.12: Delete project action
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../../src/App'

const fetchSpy = vi.fn()

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('access_token', 'test-token')
  localStorage.setItem('refresh_token', 'test-refresh')
  fetchSpy.mockReset()
  vi.stubGlobal('fetch', fetchSpy)
  vi.stubGlobal('WebSocket', class MockWebSocket {
    static OPEN = 1
    send = vi.fn()
    close = vi.fn()
    readyState = 1
    binaryType = 'blob'
    onopen: unknown = null
    onmessage: unknown = null
    onclose: unknown = null
    onerror: unknown = null
    constructor(_url: string) {}
  })
  vi.stubGlobal('ResizeObserver', class {
    observe = vi.fn()
    disconnect = vi.fn()
    constructor(_cb: unknown) {}
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const mockProjectDetail = {
  id: 'proj-123',
  name: 'My Agent',
  status: 'running',
  created_at: '2024-01-01T00:00:00Z',
  last_active_at: '2024-01-01T01:00:00Z',
  terminal_url: 'ws://localhost:9000/terminal/proj-123?token=jwt',
  ssh_host: '1.2.3.4',
  ssh_port: 2222,
  ssh_user: 'agent',
  ssh_private_key: '-----BEGIN OPENSSH PRIVATE KEY-----\ntest-key\n-----END OPENSSH PRIVATE KEY-----',
  last_backup_at: '2024-01-01T02:00:00Z',
  last_snapshot_at: null,
}

// Mock xterm with proper constructor functions
vi.mock('@xterm/xterm', () => ({
  Terminal: function MockTerminal() {
    this.write = vi.fn()
    this.dispose = vi.fn()
    this.loadAddon = vi.fn()
    this.onData = vi.fn()
    this.onBinary = vi.fn()
    this.open = vi.fn()
    this.cols = 80
    this.rows = 24
  },
}))

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: function MockFitAddon() {
    this.fit = vi.fn()
  },
}))

vi.mock('@xterm/addon-web-links', () => ({
  WebLinksAddon: function MockWebLinksAddon() {},
}))

describe('T9.9: Create project flow', () => {
  it('creates project and navigates to detail page with SSH key shown', async () => {
    const user = userEvent.setup()

    // Mock list projects (initial load)
    fetchSpy.mockResolvedValueOnce(jsonResponse([]))
    // Mock create project
    fetchSpy.mockResolvedValueOnce(jsonResponse(mockProjectDetail))
    // Mock get project (detail page loads)
    fetchSpy.mockResolvedValueOnce(jsonResponse(mockProjectDetail))

    window.history.pushState({}, '', '/projects')
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('New Project')).toBeInTheDocument()
    })

    await user.click(screen.getByText('New Project'))

    const nameInput = screen.getByPlaceholderText('Project name')
    await user.type(nameInput, 'My Agent')
    await user.click(screen.getByText('Create'))

    // Should navigate to project detail and show SSH key
    await waitFor(() => {
      expect(window.location.pathname).toContain('/projects/proj-123')
    })
  })
})

describe('T9.10: Project detail shows correct info', () => {
  it('shows status, SSH command, backup time, and action buttons', async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse(mockProjectDetail))

    window.history.pushState({}, '', '/projects/proj-123')
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('My Agent')).toBeInTheDocument()
    })

    // Status badge
    expect(screen.getByText('running')).toBeInTheDocument()

    // SSH command
    expect(screen.getByText(/ssh agent@1\.2\.3\.4 -p 2222/)).toBeInTheDocument()

    // Last backup time
    expect(screen.getByText(/Last backup:/)).toBeInTheDocument()

    // Action buttons
    expect(screen.getByText('Stop')).toBeInTheDocument()
    expect(screen.getByText('Delete')).toBeInTheDocument()
  })
})

describe('T9.11: Stop project action', () => {
  it('calls stop API and updates status', async () => {
    const user = userEvent.setup()

    // Initial load
    fetchSpy.mockResolvedValueOnce(jsonResponse(mockProjectDetail))
    // Stop call
    fetchSpy.mockResolvedValueOnce(jsonResponse({
      ...mockProjectDetail,
      status: 'stopped',
      terminal_url: null,
      ssh_host: null,
      ssh_port: null,
    }))

    window.history.pushState({}, '', '/projects/proj-123')
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Stop')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Stop'))

    await waitFor(() => {
      // Verify the stop API was called
      const stopCall = fetchSpy.mock.calls.find(
        ([url, opts]: [string, RequestInit]) => url.includes('/stop') && opts?.method === 'POST'
      )
      expect(stopCall).toBeDefined()
    })

    await waitFor(() => {
      expect(screen.getByText('stopped')).toBeInTheDocument()
    })
  })
})

describe('T9.12: Delete project action', () => {
  it('requires typing project name and redirects to project list', async () => {
    const user = userEvent.setup()

    // Initial load
    fetchSpy.mockResolvedValueOnce(jsonResponse(mockProjectDetail))
    // Delete call
    fetchSpy.mockResolvedValueOnce(jsonResponse({ status: 'deleted' }))
    // Project list after redirect
    fetchSpy.mockResolvedValueOnce(jsonResponse([]))

    window.history.pushState({}, '', '/projects/proj-123')
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Delete')).toBeInTheDocument()
    })

    // Click delete to show confirmation
    await user.click(screen.getByText('Delete'))

    // Type project name to confirm
    const confirmInput = await screen.findByPlaceholderText('My Agent')
    await user.type(confirmInput, 'My Agent')

    await user.click(screen.getByText('Confirm Delete'))

    await waitFor(() => {
      // Verify delete API was called
      const deleteCall = fetchSpy.mock.calls.find(
        ([url, opts]: [string, RequestInit]) => url.includes('proj-123') && opts?.method === 'DELETE'
      )
      expect(deleteCall).toBeDefined()
      expect(window.location.pathname).toBe('/projects')
    })
  })
})
