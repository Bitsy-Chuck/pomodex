import { getAccessToken, getRefreshToken, setTokens, clearTokens } from './auth'

const BASE_URL = import.meta.env.VITE_API_URL || ''

interface TokenResponse {
  access_token: string
  refresh_token: string
}

export interface ProjectSummary {
  id: string
  name: string
  status: string
  created_at: string
  last_active_at: string | null
}

export interface ProjectDetail extends ProjectSummary {
  terminal_url: string | null
  ssh_host: string | null
  ssh_port: number | null
  ssh_user: string
  ssh_private_key: string | null
  last_backup_at: string | null
  last_snapshot_at: string | null
}

export interface SnapshotItem {
  tag: string
  created_at: string
}

export interface BackupStatus {
  last_backup_at: string | null
  snapshot_image: string | null
  last_snapshot_at: string | null
}

class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export { ApiError }

let isRefreshing = false
let refreshPromise: Promise<boolean> | null = null

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return false

  try {
    const res = await fetch(`${BASE_URL}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
    if (!res.ok) {
      clearTokens()
      return false
    }
    const data: TokenResponse = await res.json()
    setTokens(data.access_token, data.refresh_token)
    return true
  } catch {
    clearTokens()
    return false
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAccessToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  let res = await fetch(`${BASE_URL}${path}`, { ...options, headers })

  if (res.status === 401 && token) {
    if (!isRefreshing) {
      isRefreshing = true
      refreshPromise = refreshAccessToken()
    }
    const refreshed = await refreshPromise!
    isRefreshing = false
    refreshPromise = null

    if (refreshed) {
      const newToken = getAccessToken()!
      headers['Authorization'] = `Bearer ${newToken}`
      res = await fetch(`${BASE_URL}${path}`, { ...options, headers })
    } else {
      throw new ApiError(401, 'Session expired')
    }
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, body.detail || res.statusText)
  }

  return res.json()
}

// --- Auth endpoints ---

export async function register(email: string, password: string): Promise<{ user_id: string }> {
  return request('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const data = await request<TokenResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  setTokens(data.access_token, data.refresh_token)
  return data
}

// --- Project endpoints ---

export async function listProjects(): Promise<ProjectSummary[]> {
  return request('/projects')
}

export async function createProject(name: string): Promise<ProjectDetail> {
  return request('/projects', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export async function getProject(id: string): Promise<ProjectDetail> {
  return request(`/projects/${id}`)
}

export async function stopProject(id: string): Promise<ProjectDetail> {
  return request(`/projects/${id}/stop`, { method: 'POST' })
}

export async function startProject(id: string, snapshotTag?: string): Promise<ProjectDetail> {
  return request(`/projects/${id}/start`, {
    method: 'POST',
    body: snapshotTag ? JSON.stringify({ snapshot_tag: snapshotTag }) : undefined,
  })
}

export async function deleteProject(id: string): Promise<void> {
  return request(`/projects/${id}`, { method: 'DELETE' })
}

export async function snapshotProject(id: string): Promise<ProjectDetail> {
  return request(`/projects/${id}/snapshot`, { method: 'POST' })
}

export async function restoreProject(id: string): Promise<ProjectDetail> {
  return request(`/projects/${id}/restore`, { method: 'POST' })
}

export async function listSnapshots(id: string): Promise<SnapshotItem[]> {
  return request(`/projects/${id}/snapshots`)
}

export async function getBackupStatus(id: string): Promise<BackupStatus> {
  return request(`/projects/${id}/backup-status`)
}
