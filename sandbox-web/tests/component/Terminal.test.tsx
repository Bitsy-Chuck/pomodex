/**
 * T9.13: Terminal connects to WebSocket
 * T9.14: Terminal sends resize on connect
 * T9.15: Terminal sends user input
 * T9.16: Terminal displays server output
 * T9.17: Terminal resizes on window resize
 * T9.18: Terminal handles disconnect
 * T9.19: Terminal handles binary data
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// We need to mock xterm since jsdom has no canvas
const mockWrite = vi.fn()
const mockDispose = vi.fn()
const mockLoadAddon = vi.fn()
const mockOnData = vi.fn()
const mockOnBinary = vi.fn()
let mockCols = 80
let mockRows = 24
const mockOpen = vi.fn()

vi.mock('@xterm/xterm', () => ({
  Terminal: function MockTerminal() {
    this.write = mockWrite
    this.dispose = mockDispose
    this.loadAddon = mockLoadAddon
    this.onData = mockOnData
    this.onBinary = mockOnBinary
    this.open = mockOpen
    Object.defineProperty(this, 'cols', { get: () => mockCols })
    Object.defineProperty(this, 'rows', { get: () => mockRows })
  },
}))

const mockFit = vi.fn()
vi.mock('@xterm/addon-fit', () => ({
  FitAddon: function MockFitAddon() {
    this.fit = mockFit
  },
}))

vi.mock('@xterm/addon-web-links', () => ({
  WebLinksAddon: function MockWebLinksAddon() {},
}))

// Mock WebSocket
let wsInstances: MockWebSocket[] = []

class MockWebSocket {
  static OPEN = 1
  static CLOSED = 3

  url: string
  readyState = MockWebSocket.OPEN
  binaryType = 'blob'
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  sent: (string | ArrayBuffer)[] = []

  constructor(url: string) {
    this.url = url
    wsInstances.push(this)
    // Simulate async open
    setTimeout(() => this.onopen?.(new Event('open')), 0)
  }

  send(data: string | ArrayBuffer) {
    this.sent.push(data)
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close'))
  }
}

// Mock ResizeObserver
let roCallback: ResizeObserverCallback | null = null
const mockRODisconnect = vi.fn()
vi.stubGlobal('ResizeObserver', class {
  constructor(cb: ResizeObserverCallback) {
    roCallback = cb
  }
  observe = vi.fn()
  disconnect = mockRODisconnect
})

beforeEach(() => {
  wsInstances = []
  roCallback = null
  mockWrite.mockClear()
  mockDispose.mockClear()
  mockFit.mockClear()
  mockOnData.mockClear()
  mockOpen.mockClear()
  mockRODisconnect.mockClear()
  vi.stubGlobal('WebSocket', MockWebSocket)
})

afterEach(() => {
  vi.restoreAllMocks()
})

// Helper to import and render Terminal
async function renderTerminal(url = 'ws://localhost:9000/terminal/test-id?token=jwt') {
  const { default: Terminal } = await import('../../src/components/Terminal')
  const onDisconnect = vi.fn()
  const result = render(
    <MemoryRouter>
      <Terminal wsUrl={url} onDisconnect={onDisconnect} />
    </MemoryRouter>
  )
  // Wait for effects
  await vi.waitFor(() => expect(wsInstances.length).toBeGreaterThan(0))
  return { ...result, onDisconnect, ws: wsInstances[0] }
}

describe('T9.13: Terminal connects to WebSocket', () => {
  it('creates xterm instance and opens WebSocket to terminal_url', async () => {
    const { ws } = await renderTerminal('ws://localhost:9000/terminal/proj-123?token=jwt')

    expect(mockOpen).toHaveBeenCalledOnce()
    expect(ws.url).toBe('ws://localhost:9000/terminal/proj-123?token=jwt')
    expect(mockFit).toHaveBeenCalled()
  })
})

describe('T9.14: Terminal sends resize on connect', () => {
  it('sends JSON resize message with cols and rows on WebSocket open', async () => {
    const { ws } = await renderTerminal()

    // Trigger onopen
    ws.onopen?.(new Event('open'))

    await vi.waitFor(() => {
      const resizeMsgs = ws.sent.filter(m => typeof m === 'string' && m.includes('resize'))
      expect(resizeMsgs.length).toBeGreaterThan(0)
      const parsed = JSON.parse(resizeMsgs[0] as string)
      expect(parsed.type).toBe('resize')
      expect(parsed.cols).toBe(80)
      expect(parsed.rows).toBe(24)
    })
  })
})

describe('T9.15: Terminal sends user input', () => {
  it('sends typed characters over WebSocket', async () => {
    const { ws } = await renderTerminal()

    // Get the onData callback that Terminal registered
    expect(mockOnData).toHaveBeenCalled()
    const onDataCb = mockOnData.mock.calls[0][0]

    // Simulate typing
    onDataCb('ls -la')

    expect(ws.sent).toContain('ls -la')
  })
})

describe('T9.16: Terminal displays server output', () => {
  it('writes received WebSocket message to xterm', async () => {
    const { ws } = await renderTerminal()

    // Simulate server sending text
    ws.onmessage?.(new MessageEvent('message', { data: 'hello world' }))

    expect(mockWrite).toHaveBeenCalledWith('hello world')
  })
})

describe('T9.17: Terminal resizes on window resize', () => {
  it('calls fit and sends new dimensions when ResizeObserver fires', async () => {
    const { ws } = await renderTerminal()

    mockFit.mockClear()
    ws.sent = []

    // Change dimensions
    mockCols = 120
    mockRows = 40

    // Trigger ResizeObserver
    roCallback?.([], {} as ResizeObserver)

    expect(mockFit).toHaveBeenCalled()

    await vi.waitFor(() => {
      const resizeMsgs = ws.sent.filter(m => typeof m === 'string' && m.includes('resize'))
      expect(resizeMsgs.length).toBeGreaterThan(0)
      const parsed = JSON.parse(resizeMsgs[0] as string)
      expect(parsed.cols).toBe(120)
      expect(parsed.rows).toBe(40)
    })

    // Reset for other tests
    mockCols = 80
    mockRows = 24
  })
})

describe('T9.18: Terminal handles disconnect', () => {
  it('shows disconnected message and calls onDisconnect callback', async () => {
    const { ws, onDisconnect } = await renderTerminal()

    // Simulate WebSocket close
    ws.onclose?.(new CloseEvent('close'))

    expect(mockWrite).toHaveBeenCalledWith(expect.stringContaining('Disconnected'))
    expect(onDisconnect).toHaveBeenCalled()
  })
})

describe('T9.19: Terminal handles binary data', () => {
  it('writes binary ArrayBuffer data to xterm', async () => {
    const { ws } = await renderTerminal()

    // Simulate binary terminal escape sequence (bold text)
    const encoder = new TextEncoder()
    const binaryData = encoder.encode('\x1b[1mBold Text\x1b[0m').buffer

    ws.onmessage?.(new MessageEvent('message', { data: binaryData }))

    expect(mockWrite).toHaveBeenCalledWith(expect.any(Uint8Array))
  })
})
