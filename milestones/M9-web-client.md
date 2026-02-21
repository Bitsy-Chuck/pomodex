# M9: Web Client

| Field | Value |
|-------|-------|
| **Risk** | LOW |
| **Dependencies** | M8 (Project Service API) |
| **Blocks** | None |
| **Plan sections** | 12.4 (labeled 11 in plan) |

---

## Objective

Build the React web client with authentication, project management, and a
terminal view powered by xterm.js connecting to the Terminal Proxy. This is
the primary user interface for interacting with sandbox agents.

---

## Why This Is Low Risk

- React + xterm.js is a well-understood stack
- The API contract is fully defined from M8
- xterm.js + AttachAddon handles WebSocket terminal natively
- No novel infrastructure — standard SPA

---

## Scope

**In scope:**
- React SPA (Vite or CRA)
- Auth screens (register, login)
- JWT storage in localStorage, auto-refresh
- Project list screen
- New project creation
- Project detail screen (status, SSH info, terminal URL, backup status)
- Terminal view (xterm.js connected to Terminal Proxy WebSocket)
- Responsive layout

**Out of scope:**
- Mobile app (M10)
- Advanced terminal features (split panes, session recording playback)
- Admin dashboard

---

## Deliverables

```
sandbox-web/
  src/
    api/
      client.ts                   # Typed fetch wrapper for Project Service
      auth.ts                     # JWT storage, refresh logic
    components/
      Terminal.tsx                 # xterm.js + WebSocket to Terminal Proxy
      ProjectCard.tsx              # Project summary card for list
      Layout.tsx                   # App shell, nav
    pages/
      LoginPage.tsx
      RegisterPage.tsx
      ProjectListPage.tsx
      ProjectDetailPage.tsx
    App.tsx
    main.tsx
  tests/
    unit/api/client.test.ts
    unit/api/auth.test.ts
    component/Terminal.test.tsx
    component/ProjectCard.test.tsx
    e2e/auth.spec.ts
    e2e/projects.spec.ts
    e2e/terminal.spec.ts
  package.json
  vite.config.ts
```

---

## Implementation Tasks

1. Scaffold React app (Vite + TypeScript)
2. Implement API client (typed wrapper for all Project Service endpoints)
3. Implement auth module (JWT storage, auto-refresh, logout)
4. Build auth pages (register, login) with form validation
5. Build project list page (fetch, display, create button)
6. Build project creation flow (name input → POST /projects → navigate to detail)
7. Build project detail page (status, SSH command, backup info, actions)
8. Build Terminal component (xterm.js + AttachAddon + FitAddon + WebLinksAddon)
9. Wire Terminal into project detail page
10. Add resize handling (ResizeObserver → fit terminal → send resize to ttyd)
11. Write all tests

---

## Test Cases

### API Client Tests

#### T9.1: API client handles auth headers
**Type**: Unit test
**Assert**:
- Requests include `Authorization: Bearer {token}` when token exists
- Requests without token don't include the header

#### T9.2: API client auto-refreshes expired token
**Type**: Unit test
**Setup**: Access token expired, refresh token valid
**Assert**:
- First request fails with 401
- Client calls `/auth/refresh` automatically
- Retries original request with new token
- New token stored in localStorage

#### T9.3: API client types match API contract
**Type**: Unit test
**Assert**:
- `createProject()` returns `{ project_id, status, ssh, terminal_url }`
- `listProjects()` returns array of project summaries
- `getProject(id)` returns full project detail

### Auth Tests

#### T9.4: Register flow
**Type**: E2E
**Steps**:
1. Navigate to /register
2. Fill email + password
3. Submit
**Assert**:
- On success: redirected to /login
- On duplicate email: error message shown

#### T9.5: Login flow
**Type**: E2E
**Steps**:
1. Navigate to /login
2. Fill email + password
3. Submit
**Assert**:
- On success: JWT stored in localStorage, redirected to /projects
- On failure: error message shown

#### T9.6: Protected routes redirect to login
**Type**: E2E
**Steps**:
1. Clear localStorage (no token)
2. Navigate to /projects
**Assert**:
- Redirected to /login

#### T9.7: Logout clears state
**Type**: E2E
**Steps**:
1. Login
2. Click logout
**Assert**:
- JWT removed from localStorage
- Redirected to /login

### Project Management Tests

#### T9.8: Project list displays user's projects
**Type**: Component test (mocked API)
**Setup**: Mock API returns 3 projects
**Assert**:
- 3 project cards rendered
- Each shows: name, status, created date

#### T9.9: Create project flow
**Type**: E2E
**Steps**:
1. Click "New Project"
2. Enter project name
3. Submit
**Assert**:
- Loading state shown during creation
- On success: navigated to project detail page
- SSH private key displayed (show-once UX)

#### T9.10: Project detail shows correct info
**Type**: Component test (mocked API)
**Setup**: Mock API returns project with status "running"
**Assert**:
- Status badge: "running" (green)
- SSH command displayed: `ssh agent@{host} -p {port} -i {key_file}`
- Terminal URL present
- Last backup time shown
- Action buttons: Stop, Snapshot, Delete

#### T9.11: Stop project action
**Type**: E2E
**Steps**:
1. On project detail, click "Stop"
2. Confirm dialog
**Assert**:
- API call: `POST /projects/{id}/stop`
- Status updates to "stopped"
- Terminal disconnects

#### T9.12: Delete project action
**Type**: E2E
**Steps**:
1. On project detail, click "Delete"
2. Confirm dialog (must type project name)
**Assert**:
- API call: `DELETE /projects/{id}`
- Redirected to project list
- Project no longer in list

### Terminal Tests

#### T9.13: Terminal connects to WebSocket
**Type**: Component test
**Setup**: Mock WebSocket server
**Assert**:
- xterm.js instance created and mounted
- WebSocket opened to `terminal_url`
- FitAddon applied (terminal fills container)

#### T9.14: Terminal sends resize on connect
**Type**: Component test
**Setup**: Mock WebSocket
**Assert**:
- On WebSocket open, sends JSON: `{ type: "resize", cols: N, rows: N }`
- cols/rows match terminal dimensions

#### T9.15: Terminal sends user input
**Type**: Component test
**Steps**:
1. Type "ls -la" in terminal
**Assert**:
- WebSocket.send called with the typed characters
- Characters appear in terminal

#### T9.16: Terminal displays server output
**Type**: Component test
**Steps**:
1. Mock WebSocket sends "hello world" message
**Assert**:
- "hello world" appears in xterm.js terminal display

#### T9.17: Terminal resizes on window resize
**Type**: Component test
**Steps**:
1. Trigger ResizeObserver callback
**Assert**:
- FitAddon.fit() called
- New dimensions sent via WebSocket

#### T9.18: Terminal handles disconnect
**Type**: Component test
**Steps**:
1. WebSocket closes unexpectedly
**Assert**:
- Error message shown in terminal or overlay
- Reconnect option available (or auto-reconnect)

#### T9.19: Terminal handles binary data
**Type**: Component test
**Steps**:
1. Mock WebSocket sends binary ArrayBuffer (terminal escape sequences)
**Assert**:
- xterm.js processes the binary data correctly
- Colors, cursor movement, etc. render properly

---

## Acceptance Criteria

- [ ] All 19 test cases pass
- [ ] Auth flow works: register → login → auto-refresh → logout
- [ ] Project lifecycle works from UI: create → terminal → stop → restore → delete
- [ ] Terminal is responsive and resizes with browser window
- [ ] Terminal handles disconnect/reconnect gracefully
- [ ] No JWT/token leakage (not in URLs, not in console logs)
- [ ] Works in Chrome, Firefox, Safari (latest versions)
