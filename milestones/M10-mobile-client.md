# M10: Mobile Client

| Field | Value |
|-------|-------|
| **Risk** | LOW |
| **Dependencies** | M8 (Project Service API) |
| **Blocks** | None |
| **Plan sections** | 12.5 (labeled 11 in plan) |

---

## Objective

Build the React Native mobile client with authentication, project management,
and a terminal view using WebView + inline xterm.js HTML. SSH private keys and
JWT tokens stored securely in device Keychain/Keystore.

---

## Why This Is Low Risk

- React Native is well-understood
- API contract is identical to web client
- Terminal via WebView + inline HTML is a proven approach
- Secure storage via react-native-keychain is standard

---

## Scope

**In scope:**
- React Native app (iOS + Android)
- Auth screens (register, login)
- Secure token storage (Keychain/Keystore)
- Project list screen
- Project creation + SSH key storage
- Project detail screen
- Terminal screen (WebView + inline xterm.js HTML)
- Navigation (React Navigation)

**Out of scope:**
- Web client (M9)
- Native terminal rendering (future optimization)
- Push notifications for project status changes
- SSH client (connect to sandbox via SSH from device — future feature)

---

## Deliverables

```
sandbox-mobile/
  src/
    api/
      client.ts                    # Typed fetch wrapper (shared logic with web)
    screens/
      AuthScreen.tsx               # Login + Register tabs
      ProjectListScreen.tsx
      ProjectDetailScreen.tsx
      TerminalScreen.tsx           # WebView with inline xterm.js
    storage/
      keychain.ts                  # JWT + SSH key secure storage
    navigation/
      AppNavigator.tsx
    components/
      ProjectCard.tsx
    utils/
      terminal-html.ts             # Inline HTML template for xterm.js
  tests/
    unit/api/client.test.ts
    unit/storage/keychain.test.ts
    component/AuthScreen.test.tsx
    component/ProjectListScreen.test.tsx
    component/TerminalScreen.test.tsx
    e2e/auth.e2e.ts
    e2e/projects.e2e.ts
  App.tsx
  package.json
```

---

## Implementation Tasks

1. Scaffold React Native app (Expo or bare workflow)
2. Set up React Navigation (auth stack + main stack)
3. Implement Keychain module (store/retrieve JWT + SSH keys)
4. Implement API client (same interface as web, different storage backend)
5. Build auth screen (register + login tabs, form validation)
6. Build project list screen (pull-to-refresh, create button)
7. Build project creation flow (name → API → store SSH key in Keychain)
8. Build project detail screen (status, SSH info, actions)
9. Build terminal HTML template (inline xterm.js + WebSocket connection)
10. Build terminal screen (WebView rendering the HTML template)
11. Write all tests

---

## Test Cases

### Secure Storage Tests

#### T10.1: JWT stored in Keychain
**Type**: Unit test
**Steps**:
1. Call `storeTokens(accessToken, refreshToken)`
2. Call `getAccessToken()`
**Assert**:
- Returns the stored access token
- Token not accessible from other apps (Keychain isolation)

#### T10.2: SSH private key stored in Keychain
**Type**: Unit test
**Steps**:
1. Call `storeSSHKey(projectId, privateKey)`
2. Call `getSSHKey(projectId)`
**Assert**:
- Returns the stored private key
- Key is project-scoped (different key per project)

#### T10.3: Logout clears tokens
**Type**: Unit test
**Steps**:
1. Store tokens
2. Call `clearTokens()`
3. Call `getAccessToken()`
**Assert**:
- Returns null/undefined
- Keychain entries removed

#### T10.4: Token auto-refresh
**Type**: Unit test
**Setup**: Access token expired, refresh token valid
**Assert**:
- API client calls `/auth/refresh`
- New tokens stored in Keychain
- Original request retried

### Auth Tests

#### T10.5: Register screen
**Type**: Component test
**Assert**:
- Email and password inputs rendered
- Validation: email format, password minimum length
- Submit button calls API
- Success: navigates to login
- Error: shows error message

#### T10.6: Login screen
**Type**: Component test
**Assert**:
- Email and password inputs rendered
- Submit calls API
- Success: tokens stored in Keychain, navigates to project list
- Error: shows error message

#### T10.7: Auth stack — unauthenticated
**Type**: Component test
**Setup**: No tokens in Keychain
**Assert**:
- App shows auth screen (login/register)
- Cannot navigate to project screens

#### T10.8: Auth stack — authenticated
**Type**: Component test
**Setup**: Valid tokens in Keychain
**Assert**:
- App shows project list screen
- Auth screens not shown

### Project Tests

#### T10.9: Project list — pull to refresh
**Type**: Component test (mocked API)
**Assert**:
- Initial load fetches projects
- Pull-to-refresh triggers new fetch
- Projects displayed with name, status, date

#### T10.10: Create project
**Type**: E2E
**Steps**:
1. Tap "New Project"
2. Enter name
3. Submit
**Assert**:
- Loading indicator during creation
- On success: SSH private key shown + stored in Keychain
- Navigated to project detail

#### T10.11: Project detail screen
**Type**: Component test (mocked API)
**Assert**:
- Shows: status badge, project name, created date
- Shows: SSH command with port
- Shows: "Open Terminal" button (when running)
- Shows: Stop/Start/Delete action buttons
- Shows: last backup time

#### T10.12: Stop project
**Type**: Component test
**Steps**:
1. Tap "Stop"
2. Confirm
**Assert**:
- API call made
- Status updates to "stopped"
- Terminal button disabled

#### T10.13: Delete project
**Type**: Component test
**Steps**:
1. Tap "Delete"
2. Confirm (type project name)
**Assert**:
- API call made
- SSH key removed from Keychain
- Navigated back to project list

### Terminal Tests

#### T10.14: Terminal HTML template generation
**Type**: Unit test
**Steps**:
1. Call `generateTerminalHTML(terminalUrl)`
**Assert**:
- Returns valid HTML string
- Contains xterm.js and xterm-addon-fit CDN links
- Contains WebSocket connection to `terminalUrl`
- Contains resize handler
- Has dark background theme

#### T10.15: Terminal WebView renders
**Type**: Component test
**Setup**: Mock WebView component
**Steps**:
1. Render TerminalScreen with terminalUrl
**Assert**:
- WebView rendered with `source={{ html: ... }}`
- `keyboardDisplayRequiresUserAction` is false (allow keyboard without tap)
- `mixedContentMode` is "always"

#### T10.16: Terminal WebSocket connects
**Type**: E2E
**Setup**: Running project with terminal proxy
**Steps**:
1. Open terminal screen
2. Wait for WebSocket connection
**Assert**:
- Terminal shows shell prompt
- User can type commands
- Output appears in terminal

#### T10.17: Terminal handles device rotation
**Type**: Component test
**Steps**:
1. Render terminal in portrait
2. Rotate to landscape
**Assert**:
- WebView resizes
- xterm.js fit addon recalculates dimensions
- No content loss

#### T10.18: Terminal survives app background/foreground
**Type**: E2E
**Steps**:
1. Open terminal, type a command
2. Background the app for 5 seconds
3. Foreground the app
**Assert**:
- Terminal view still visible
- WebSocket may reconnect if disconnected
- Previous output still visible

---

## Acceptance Criteria

- [ ] All 18 test cases pass
- [ ] Auth flow works: register → login → auto-refresh → logout
- [ ] JWT and SSH keys stored securely in Keychain/Keystore
- [ ] Project lifecycle works from mobile: create → terminal → stop → restore → delete
- [ ] Terminal is usable on both phone and tablet screen sizes
- [ ] Works on iOS 15+ and Android 12+
- [ ] App handles network interruptions gracefully (error messages, retry options)
- [ ] No tokens or keys logged or leaked to console
