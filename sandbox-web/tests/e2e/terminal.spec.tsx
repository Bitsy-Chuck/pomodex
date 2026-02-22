/**
 * T9.13-T9.19 are covered in tests/component/Terminal.test.tsx
 * This file verifies the terminal integration within the project detail page.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import App from '../../src/App'

const fetchSpy = vi.fn()

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('access_token', 'test-token')
  localStorage.setItem('refresh_token', 'test-refresh')
  fetchSpy.mockReset()
  vi.stubGlobal('fetch', fetchSpy)
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

let createdWsUrls: string[] = []
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
  constructor(url: string) {
    createdWsUrls.push(url)
  }
})

vi.stubGlobal('ResizeObserver', class {
  observe = vi.fn()
  disconnect = vi.fn()
  constructor(_cb: unknown) {}
})

describe('Terminal integration in project detail', () => {
  beforeEach(() => {
    createdWsUrls = []
  })

  it('renders terminal when project is running with terminal_url', async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse({
      id: 'proj-1',
      name: 'Test Project',
      status: 'running',
      created_at: '2024-01-01T00:00:00Z',
      last_active_at: null,
      terminal_url: 'ws://localhost:9000/terminal/proj-1?token=jwt',
      ssh_host: '0.0.0.0',
      ssh_port: 2222,
      ssh_user: 'agent',
      ssh_private_key: null,
      last_backup_at: null,
      last_snapshot_at: null,
    }))

    window.history.pushState({}, '', '/projects/proj-1')
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Terminal')).toBeInTheDocument()
    })

    // WebSocket should have been created with the terminal URL
    expect(createdWsUrls).toContain('ws://localhost:9000/terminal/proj-1?token=jwt')
  })

  it('does not render terminal when project is stopped', async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse({
      id: 'proj-2',
      name: 'Stopped Project',
      status: 'stopped',
      created_at: '2024-01-01T00:00:00Z',
      last_active_at: null,
      terminal_url: null,
      ssh_host: null,
      ssh_port: null,
      ssh_user: 'agent',
      ssh_private_key: null,
      last_backup_at: null,
      last_snapshot_at: null,
    }))

    window.history.pushState({}, '', '/projects/proj-2')
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Stopped Project')).toBeInTheDocument()
    })

    expect(screen.queryByText('Terminal')).not.toBeInTheDocument()
  })
})
