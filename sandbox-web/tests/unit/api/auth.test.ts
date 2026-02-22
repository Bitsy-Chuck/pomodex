/**
 * Tests for auth module (JWT storage, helpers)
 * Supports T9.1 indirectly — token storage/retrieval
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  getAccessToken, getRefreshToken,
  setTokens, clearTokens, isLoggedIn,
} from '../../../src/api/auth'

beforeEach(() => {
  localStorage.clear()
})

describe('auth module', () => {
  it('stores and retrieves tokens', () => {
    setTokens('access-123', 'refresh-456')
    expect(getAccessToken()).toBe('access-123')
    expect(getRefreshToken()).toBe('refresh-456')
  })

  it('returns null when no tokens stored', () => {
    expect(getAccessToken()).toBeNull()
    expect(getRefreshToken()).toBeNull()
  })

  it('clears tokens', () => {
    setTokens('a', 'b')
    clearTokens()
    expect(getAccessToken()).toBeNull()
    expect(getRefreshToken()).toBeNull()
  })

  it('isLoggedIn returns true when token exists', () => {
    expect(isLoggedIn()).toBe(false)
    setTokens('a', 'b')
    expect(isLoggedIn()).toBe(true)
  })
})
