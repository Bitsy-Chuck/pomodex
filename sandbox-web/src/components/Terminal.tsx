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

let _wsSeq = 0

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

    const id = ++_wsSeq
    const log = (msg: string, ...args: unknown[]) =>
      console.log(`[Terminal ws:${id}] ${msg}`, ...args)

    log('effect MOUNT wsUrl=%s', wsUrl)
    let cancelled = false

    const term = new XTerm({ cursorBlink: true, fontSize: 14 })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.loadAddon(new WebLinksAddon())
    term.open(containerRef.current)
    fit.fit()

    termRef.current = term
    fitRef.current = fit

    log('creating WebSocket')
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.binaryType = 'arraybuffer'

    log('readyState after new: %d (0=CONNECTING)', ws.readyState)

    ws.onopen = () => {
      log('onopen (cancelled=%s, readyState=%d)', cancelled, ws.readyState)
      if (cancelled) { ws.close(); return }
      // ttyd 1.7+ expects the first message to be a JSON auth+resize payload (no type prefix)
      const encoder = new TextEncoder()
      const authPayload = JSON.stringify({ AuthToken: '', columns: term.cols, rows: term.rows })
      log('sending auth: %s', authPayload)
      ws.send(encoder.encode(authPayload))
    }

    let msgCount = 0
    ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        log('onmessage string (ignored): %s', event.data.slice(0, 100))
        return
      }
      const view = new Uint8Array(event.data as ArrayBuffer)
      if (view.length === 0) return

      msgCount++
      const msgType = view[0]
      const payload = view.slice(1)

      if (msgCount <= 5) {
        log('onmessage #%d type=%d len=%d', msgCount, msgType, view.length)
      }

      if (msgType === TTYD_OUTPUT) {
        term.write(payload)
      }
      // TTYD_SET_WINDOW_TITLE and TTYD_SET_PREFERENCES are silently ignored
    }

    ws.onclose = (event) => {
      log('onclose code=%d reason=%s wasClean=%s cancelled=%s (received %d msgs)',
        event.code, JSON.stringify(event.reason), event.wasClean, cancelled, msgCount)
      if (cancelled) return
      term.write('\r\n\x1b[31m[Disconnected]\x1b[0m\r\n')
      onDisconnect?.()
    }

    ws.onerror = (event) => {
      log('onerror readyState=%d cancelled=%s event=%o', ws.readyState, cancelled, event)
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
      log('effect CLEANUP (readyState=%d, cancelled_was=%s, received %d msgs)',
        ws.readyState, cancelled, msgCount)
      cancelled = true
      ro.disconnect()
      ws.close()
      term.dispose()
    }
  }, [wsUrl, onDisconnect])

  return <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 400 }} />
}
