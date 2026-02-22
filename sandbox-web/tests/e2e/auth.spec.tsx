/**
 * T9.4: Register flow
 * T9.5: Login flow
 * T9.6: Protected routes redirect to login
 * T9.7: Logout clears state
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../../src/App'

const fetchSpy = vi.fn()

beforeEach(() => {
  localStorage.clear()
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

describe('T9.4: Register flow', () => {
  it('on success: redirected to /login', async () => {
    const user = userEvent.setup()

    // Mock register endpoint
    fetchSpy.mockResolvedValueOnce(jsonResponse({ user_id: 'uuid-123' }))

    // Start at /register using window.history
    window.history.pushState({}, '', '/register')
    render(<App />)

    await user.type(screen.getByLabelText('Email'), 'test@example.com')
    await user.type(screen.getByLabelText('Password'), 'SecurePass123!')
    await user.click(screen.getByRole('button', { name: /register/i }))

    await waitFor(() => {
      expect(window.location.pathname).toBe('/login')
    })
  })

  it('on duplicate email: error message shown', async () => {
    const user = userEvent.setup()

    fetchSpy.mockResolvedValueOnce(jsonResponse({ detail: 'Email already registered' }, 409))

    window.history.pushState({}, '', '/register')
    render(<App />)

    await user.type(screen.getByLabelText('Email'), 'dupe@example.com')
    await user.type(screen.getByLabelText('Password'), 'Pass123!')
    await user.click(screen.getByRole('button', { name: /register/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Email already registered')
    })
  })
})

describe('T9.5: Login flow', () => {
  it('on success: JWT stored, redirected to /projects', async () => {
    const user = userEvent.setup()

    // Mock login then project list
    fetchSpy.mockResolvedValueOnce(jsonResponse({
      access_token: 'jwt-access',
      refresh_token: 'jwt-refresh',
    }))
    fetchSpy.mockResolvedValueOnce(jsonResponse([]))

    window.history.pushState({}, '', '/login')
    render(<App />)

    await user.type(screen.getByLabelText('Email'), 'test@example.com')
    await user.type(screen.getByLabelText('Password'), 'SecurePass123!')
    await user.click(screen.getByRole('button', { name: /login/i }))

    await waitFor(() => {
      expect(localStorage.getItem('access_token')).toBe('jwt-access')
      expect(localStorage.getItem('refresh_token')).toBe('jwt-refresh')
      expect(window.location.pathname).toBe('/projects')
    })
  })

  it('on failure: error message shown', async () => {
    const user = userEvent.setup()

    fetchSpy.mockResolvedValueOnce(jsonResponse({ detail: 'Invalid credentials' }, 401))

    window.history.pushState({}, '', '/login')
    render(<App />)

    await user.type(screen.getByLabelText('Email'), 'bad@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: /login/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Invalid credentials')
    })
  })
})

describe('T9.6: Protected routes redirect to login', () => {
  it('redirects to /login when no token exists', async () => {
    localStorage.clear() // ensure no token

    window.history.pushState({}, '', '/projects')
    render(<App />)

    await waitFor(() => {
      expect(window.location.pathname).toBe('/login')
    })
  })
})

describe('T9.7: Logout clears state', () => {
  it('removes JWT and redirects to /login', async () => {
    const user = userEvent.setup()

    // Start logged in
    localStorage.setItem('access_token', 'test-token')
    localStorage.setItem('refresh_token', 'test-refresh')

    // Mock project list fetch
    fetchSpy.mockResolvedValueOnce(jsonResponse([]))

    window.history.pushState({}, '', '/projects')
    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Logout')).toBeInTheDocument()
    })

    await user.click(screen.getByText('Logout'))

    await waitFor(() => {
      expect(localStorage.getItem('access_token')).toBeNull()
      expect(localStorage.getItem('refresh_token')).toBeNull()
      expect(window.location.pathname).toBe('/login')
    })
  })
})
