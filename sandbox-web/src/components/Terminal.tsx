import { useEffect, useRef } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'

// ttyd 1.7+ protocol uses ASCII character type bytes
const TTYD_OUTPUT = '0'.charCodeAt(0)           // 48 (server → client)
const TTYD_SET_WINDOW_TITLE = '1'.charCodeAt(0) // 49
const TTYD_SET_PREFERENCES = '2'.charCodeAt(0)  // 50

const TTYD_INPUT = '0'.charCodeAt(0)            // 48 (client → server)
const TTYD_RESIZE = '1'.charCodeAt(0)           // 49

interface TerminalProps {
  wsUrl: string
  onDisconnect?: () => void
}

function sendTtyd(ws: WebSocket, type: number, data: string) {
  const encoder = new TextEncoder()
  const payload = encoder.encode(data)
  const buf = new Uint8Array(payload.length + 1)
  buf[0] = type
  buf.set(payload, 1)
  ws.send(buf.buffer)
}

export default function Terminal({ wsUrl, onDisconnect }: TerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<XTerm | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const fitRef = useRef<FitAddon | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    let cancelled = false

    const term = new XTerm({ cursorBlink: true, fontSize: 14 })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.loadAddon(new WebLinksAddon())
    term.open(containerRef.current)
    fit.fit()

    termRef.current = term
    fitRef.current = fit

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.binaryType = 'arraybuffer'

    ws.onopen = () => {
      if (cancelled) { ws.close(); return }
      // ttyd 1.7+ expects the first message to be a JSON auth+resize payload (no type prefix)
      const encoder = new TextEncoder()
      ws.send(encoder.encode(JSON.stringify({ AuthToken: '', columns: term.cols, rows: term.rows })))
    }

    ws.onmessage = (event) => {
      if (typeof event.data === 'string') return
      const view = new Uint8Array(event.data as ArrayBuffer)
      if (view.length === 0) return

      const msgType = view[0]
      const payload = view.slice(1)

      if (msgType === TTYD_OUTPUT) {
        term.write(payload)
      }
      // TTYD_SET_WINDOW_TITLE and TTYD_SET_PREFERENCES are silently ignored
    }

    ws.onclose = () => {
      if (cancelled) return
      term.write('\r\n\x1b[31m[Disconnected]\x1b[0m\r\n')
      onDisconnect?.()
    }

    ws.onerror = () => {
      if (cancelled) return
      term.write('\r\n\x1b[31m[Connection error]\x1b[0m\r\n')
    }

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        sendTtyd(ws, TTYD_INPUT, data)
      }
    })

    // Resize handling
    const ro = new ResizeObserver(() => {
      fit.fit()
      if (ws.readyState === WebSocket.OPEN) {
        sendTtyd(ws, TTYD_RESIZE, JSON.stringify({ columns: term.cols, rows: term.rows }))
      }
    })
    ro.observe(containerRef.current)

    return () => {
      cancelled = true
      ro.disconnect()
      ws.close()
      term.dispose()
    }
  }, [wsUrl, onDisconnect])

  return <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 400 }} />
}
