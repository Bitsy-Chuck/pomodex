/**
 * T9.1: API client handles auth headers
 * T9.2: API client auto-refreshes expired token
 * T9.3: API client types match API contract
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setTokens, clearTokens, getAccessToken } from '../../../src/api/auth'
import {
  listProjects, createProject, getProject,
  type ProjectSummary, type ProjectDetail,
} from '../../../src/api/client'

// Track fetch calls
const fetchSpy = vi.fn()

beforeEach(() => {
  clearTokens()
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

describe('T9.1: API client handles auth headers', () => {
  it('includes Authorization header when token exists', async () => {
    setTokens('my-access-token', 'my-refresh-token')
    fetchSpy.mockResolvedValueOnce(jsonResponse([]))

    await listProjects()

    expect(fetchSpy).toHaveBeenCalledOnce()
    const [, options] = fetchSpy.mock.calls[0]
    expect(options.headers['Authorization']).toBe('Bearer my-access-token')
  })

  it('does not include Authorization header when no token', async () => {
    fetchSpy.mockResolvedValueOnce(jsonResponse({ user_id: '123' }))

    // register doesn't require auth — but our client always adds token if present
    // With no token, the header should not be present
    const { register } = await import('../../../src/api/client')
    await register('test@example.com', 'pass')

    const [, options] = fetchSpy.mock.calls[0]
    expect(options.headers['Authorization']).toBeUndefined()
  })
})

describe('T9.2: API client auto-refreshes expired token', () => {
  it('refreshes token on 401 and retries the request', async () => {
    setTokens('expired-token', 'valid-refresh-token')

    // 1st call: 401 (expired access token)
    fetchSpy.mockResolvedValueOnce(jsonResponse({ detail: 'Unauthorized' }, 401))
    // 2nd call: refresh endpoint succeeds
    fetchSpy.mockResolvedValueOnce(jsonResponse({
      access_token: 'new-access-token',
      refresh_token: 'new-refresh-token',
    }))
    // 3rd call: retry original request with new token
    fetchSpy.mockResolvedValueOnce(jsonResponse([]))

    await listProjects()

    expect(fetchSpy).toHaveBeenCalledTimes(3)

    // Verify refresh was called
    const [refreshUrl] = fetchSpy.mock.calls[1]
    expect(refreshUrl).toContain('/auth/refresh')

    // Verify retry used new token
    const [, retryOptions] = fetchSpy.mock.calls[2]
    expect(retryOptions.headers['Authorization']).toBe('Bearer new-access-token')

    // Verify tokens were stored
    expect(getAccessToken()).toBe('new-access-token')
  })
})

describe('T9.3: API client types match API contract', () => {
  it('createProject returns project detail with expected fields', async () => {
    setTokens('token', 'refresh')
    const mockProject: ProjectDetail = {
      id: '550e8400-e29b-41d4-a716-446655440000',
      name: 'My Agent',
      status: 'running',
      created_at: '2024-01-01T00:00:00Z',
      last_active_at: '2024-01-01T01:00:00Z',
      terminal_url: 'ws://localhost:9000/terminal/550e8400-e29b-41d4-a716-446655440000',
      ssh_host: '0.0.0.0',
      ssh_port: 2222,
      ssh_user: 'agent',
      ssh_private_key: '-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----',
      last_backup_at: null,
      last_snapshot_at: null,
    }

    fetchSpy.mockResolvedValueOnce(jsonResponse(mockProject))

    const result = await createProject('My Agent')

    expect(result).toHaveProperty('id')
    expect(result).toHaveProperty('status')
    expect(result).toHaveProperty('ssh_host')
    expect(result).toHaveProperty('terminal_url')
    expect(result.ssh_user).toBe('agent')
  })

  it('listProjects returns array of project summaries', async () => {
    setTokens('token', 'refresh')
    const mockList: ProjectSummary[] = [
      { id: 'p1', name: 'Agent 1', status: 'running', created_at: '2024-01-01T00:00:00Z', last_active_at: null },
      { id: 'p2', name: 'Agent 2', status: 'stopped', created_at: '2024-01-02T00:00:00Z', last_active_at: null },
    ]

    fetchSpy.mockResolvedValueOnce(jsonResponse(mockList))

    const result = await listProjects()

    expect(Array.isArray(result)).toBe(true)
    expect(result).toHaveLength(2)
    expect(result[0]).toHaveProperty('id')
    expect(result[0]).toHaveProperty('name')
    expect(result[0]).toHaveProperty('status')
    expect(result[0]).toHaveProperty('created_at')
  })

  it('getProject returns full project detail', async () => {
    setTokens('token', 'refresh')
    const mockDetail: ProjectDetail = {
      id: 'p1',
      name: 'Agent 1',
      status: 'running',
      created_at: '2024-01-01T00:00:00Z',
      last_active_at: '2024-01-01T01:00:00Z',
      terminal_url: 'ws://localhost:9000/terminal/p1',
      ssh_host: '0.0.0.0',
      ssh_port: 2222,
      ssh_user: 'agent',
      ssh_private_key: 'key-data',
      last_backup_at: '2024-01-01T02:00:00Z',
      last_snapshot_at: null,
    }

    fetchSpy.mockResolvedValueOnce(jsonResponse(mockDetail))

    const result = await getProject('p1')

    expect(result.id).toBe('p1')
    expect(result.terminal_url).toContain('terminal')
    expect(result.ssh_host).toBeDefined()
    expect(result.ssh_port).toBe(2222)
    expect(result.last_backup_at).toBeDefined()
  })
})
