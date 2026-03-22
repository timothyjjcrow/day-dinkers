import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'

import { chromium, devices } from 'playwright'

const baseUrl = String(process.env.THIRDSHOT_BASE_URL || 'http://127.0.0.1:5001').replace(/\/$/, '')
const outputDir = process.env.THIRDSHOT_SMOKE_OUTPUT || '/tmp/thirdshot-schedule-smoke'
const device = devices['iPhone 13']

async function ensureDir(dirPath) {
  await fs.mkdir(dirPath, { recursive: true })
}

function uniqueLabel(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function escapeForRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

async function request(pathname, { method = 'GET', token, csrf, body } = {}) {
  const headers = {}
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  if (csrf) {
    headers['X-CSRF-Token'] = csrf
  }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  const response = await fetch(`${baseUrl}${pathname}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  const text = await response.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    throw new Error(`${method} ${pathname} failed (${response.status}): ${typeof payload === 'string' ? payload : JSON.stringify(payload)}`)
  }

  return payload
}

async function registerUser(label) {
  const username = uniqueLabel(label)
  const email = `${username}@example.com`
  const password = 'Playtest123'
  const auth = await request('/api/auth/register', {
    method: 'POST',
    body: {
      username,
      email,
      password,
      name: label.replace(/_/g, ' '),
    },
  })
  return {
    token: auth.token,
    user: auth.user,
    email,
    password,
  }
}

async function csrfFor(token) {
  const payload = await request('/api/auth/csrf', { token })
  return payload.csrf_token
}

async function authedRequest(token, pathname, { method = 'GET', body } = {}) {
  const csrf = method === 'GET' || method === 'HEAD' ? undefined : await csrfFor(token)
  return request(pathname, {
    method,
    token,
    csrf,
    body,
  })
}

async function makeFriends(host, other) {
  await authedRequest(host.token, '/api/auth/friends/request', {
    method: 'POST',
    body: {
      friend_id: other.user.id,
    },
  })
  const pending = await authedRequest(other.token, '/api/auth/friends/pending')
  const requestRow = pending.requests.find((row) => row.user?.id === host.user.id)
  assert(requestRow, `Expected pending friend request for ${other.user.username}`)
  await authedRequest(other.token, '/api/auth/friends/respond', {
    method: 'POST',
    body: {
      friendship_id: requestRow.id,
      action: 'accept',
    },
  })
}

async function seedSession(page, auth) {
  await page.goto(`${baseUrl}/map`, { waitUntil: 'domcontentloaded' })
  await page.evaluate(({ token, user }) => {
    window.localStorage.setItem('thirdshot_token', token)
    window.localStorage.setItem('thirdshot_user', JSON.stringify(user))
  }, { token: auth.token, user: auth.user })
  await page.goto(`${baseUrl}/map`, { waitUntil: 'networkidle' })
}

async function openNotifications(page) {
  await page.getByRole('button', { name: 'Notifications' }).click()
  await page.getByRole('dialog', { name: 'Alerts' }).waitFor()
}

async function closeOpenDialog(page, name) {
  const dialog = page.getByRole('dialog', { name })
  await dialog.getByRole('button', { name: 'Close' }).click()
  await dialog.waitFor({ state: 'hidden' })
}

async function waitForNotification(token, type, referenceId, timeoutMs = 10000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const payload = await authedRequest(token, '/api/auth/notifications')
    const notification = payload.notifications.find((row) => row.notif_type === type && Number(row.reference_id) === Number(referenceId))
    if (notification) return notification
    await new Promise((resolve) => setTimeout(resolve, 300))
  }
  throw new Error(`Timed out waiting for ${type} notification for reference ${referenceId}`)
}

async function dialogOverflowMetrics(locator) {
  return locator.evaluate((node) => {
    const dialogRect = node.getBoundingClientRect()
    const descendantOverflow = Array.from(node.querySelectorAll('*')).some((element) => {
      const rect = element.getBoundingClientRect()
      return rect.right > dialogRect.right + 0.5
    })

    return {
      dialogClientWidth: node.clientWidth,
      dialogScrollWidth: node.scrollWidth,
      bodyClientWidth: document.body.clientWidth,
      bodyScrollWidth: document.body.scrollWidth,
      rootClientWidth: document.documentElement.clientWidth,
      rootScrollWidth: document.documentElement.scrollWidth,
      descendantOverflow,
    }
  })
}

function assertNoOverflow(metrics, label) {
  assert.equal(metrics.dialogClientWidth, metrics.dialogScrollWidth, `${label} dialog overflowed horizontally`)
  assert.equal(metrics.bodyClientWidth, metrics.bodyScrollWidth, `${label} body overflowed horizontally`)
  assert.equal(metrics.rootClientWidth, metrics.rootScrollWidth, `${label} root overflowed horizontally`)
  assert.equal(metrics.descendantOverflow, false, `${label} had clipped descendants`)
}

async function run() {
  await ensureDir(outputDir)

  const host = await registerUser('Host Tester')
  const friendA = await registerUser('Friend Alpha')
  const friendB = await registerUser('Friend Bravo')

  await makeFriends(host, friendA)
  await makeFriends(host, friendB)

  const browser = await chromium.launch({ headless: true })
  const hostContext = await browser.newContext({ ...device })
  const friendAContext = await browser.newContext({ ...device })
  const friendBContext = await browser.newContext({ ...device })

  const hostPage = await hostContext.newPage()
  const friendAPage = await friendAContext.newPage()
  const friendBPage = await friendBContext.newPage()

  try {
    await Promise.all([
      seedSession(hostPage, host),
      seedSession(friendAPage, friendA),
      seedSession(friendBPage, friendB),
    ])

    await hostPage.getByRole('button', { name: 'New', exact: true }).click()
    const composer = hostPage.getByRole('dialog', { name: 'Schedule game' })
    await composer.waitFor()

    const composerMetrics = await dialogOverflowMetrics(composer)
    assertNoOverflow(composerMetrics, 'Schedule composer')

    const inviteToggle = composer.getByRole('button', { name: /^(Add|Done)$/i })
    await inviteToggle.waitFor()
    if (/add/i.test((await inviteToggle.textContent()) || '')) {
      await inviteToggle.click()
    }
    await composer.getByRole('button', { name: new RegExp(`${friendA.user.name}.*Invite`, 'i') }).click()
    const detailsToggle = composer.getByRole('button', { name: /More options|Hide details/i })
    await detailsToggle.waitFor()
    if (/more options/i.test((await detailsToggle.textContent()) || '')) {
      await detailsToggle.click()
    }
    const sessionTitle = uniqueLabel('Multi User Ladder')
    await composer.getByLabel('Name (optional)').fill(sessionTitle)

    const createResponsePromise = hostPage.waitForResponse((response) => (
      response.url() === `${baseUrl}/api/sessions` && response.request().method() === 'POST'
    ))
    await composer.getByRole('button', { name: /Save \+ invite 1/i }).click()
    const createResponse = await createResponsePromise
    const createPayload = await createResponse.json()
    const sessionId = createPayload.session.id
    const sessionDetails = await authedRequest(host.token, `/api/sessions/${sessionId}`)
    const courtId = sessionDetails.session.court_id
    const sessionDateLabel = new Date(sessionDetails.session.start_time).toLocaleDateString([], {
      month: 'short',
      day: 'numeric',
    })

    await hostPage.goto(`${baseUrl}/courts/${courtId}`, { waitUntil: 'networkidle' })
    const courtScheduleDayButton = hostPage.getByRole('button', {
      name: new RegExp(escapeForRegex(sessionDateLabel).replace(/\s+/g, '\\s+'), 'i'),
    }).first()
    await courtScheduleDayButton.click()
    const dayDialog = hostPage.getByRole('dialog', { name: 'Day schedule' })
    await dayDialog.waitFor()
    const daySheetMetrics = await dialogOverflowMetrics(dayDialog)
    assertNoOverflow(daySheetMetrics, 'Day schedule')
    await dayDialog.getByRole('button', { name: 'Close' }).click({ force: true })

    await hostPage.goto(`${baseUrl}/courts/${courtId}?session=${sessionId}`, { waitUntil: 'networkidle' })
    const hostGameDialog = hostPage.getByRole('dialog', { name: 'Game' })
    await hostGameDialog.waitFor()
    const hostGameMetrics = await dialogOverflowMetrics(hostGameDialog)
    assertNoOverflow(hostGameMetrics, 'Host game sheet')
    await hostGameDialog.getByRole('button', { name: /Invite friends/i }).click()
    await hostGameDialog.getByRole('button', { name: new RegExp(`${friendB.user.name}.*Invite`, 'i') }).click()
    const laterInviteResponsePromise = hostPage.waitForResponse((response) => (
      response.url() === `${baseUrl}/api/sessions/${sessionId}/invite` && response.request().method() === 'POST'
    ))
    await hostGameDialog.getByRole('button', { name: /^Invite 1 friend$/i }).click()
    await laterInviteResponsePromise

    const friendANotification = await waitForNotification(friendA.token, 'session_invite', sessionId)
    const friendBNotification = await waitForNotification(friendB.token, 'session_invite', sessionId)
    assert.equal(friendANotification.target_path, `/courts/${courtId}?session=${sessionId}`)
    assert.equal(friendBNotification.target_path, `/courts/${courtId}?session=${sessionId}`)

    await openNotifications(friendAPage)
    await closeOpenDialog(friendAPage, 'Alerts')
    await openNotifications(friendAPage)
    await friendAPage.getByRole('button', { name: /session invite/i }).first().click()
    await friendAPage.waitForURL(new RegExp(`/courts/${courtId}\\?session=${sessionId}`))
    await friendAPage.waitForLoadState('networkidle')
    const friendAGameDialog = friendAPage.getByRole('dialog', { name: 'Game' })
    await friendAGameDialog.waitFor()
    const friendAGameMetricsBeforeAccept = await dialogOverflowMetrics(friendAGameDialog)
    assertNoOverflow(friendAGameMetricsBeforeAccept, 'Friend A game sheet before accept')
    const joinResponsePromise = friendAPage.waitForResponse((response) => (
      response.url() === `${baseUrl}/api/sessions/${sessionId}/join` && response.request().method() === 'POST'
    ))
    await friendAGameDialog.getByRole('button', { name: /Accept Invite/i }).click()
    await joinResponsePromise
    await friendAGameDialog.getByRole('button', { name: /^Open Court$/i }).waitFor()
    const friendAGameMetricsAfterAccept = await dialogOverflowMetrics(friendAGameDialog)
    assertNoOverflow(friendAGameMetricsAfterAccept, 'Friend A game sheet after accept')

    await openNotifications(friendBPage)
    await closeOpenDialog(friendBPage, 'Alerts')
    await openNotifications(friendBPage)
    await friendBPage.getByRole('button', { name: /session invite/i }).first().click()
    await friendBPage.waitForURL(new RegExp(`/courts/${courtId}\\?session=${sessionId}`))
    await friendBPage.waitForLoadState('networkidle')
    const friendBGameDialog = friendBPage.getByRole('dialog', { name: 'Game' })
    await friendBGameDialog.waitFor()
    const friendBGameMetrics = await dialogOverflowMetrics(friendBGameDialog)
    assertNoOverflow(friendBGameMetrics, 'Friend B game sheet')

    const updatedSession = await authedRequest(host.token, `/api/sessions/${sessionId}`)
    const joinedPlayerIds = updatedSession.session.players
      .filter((player) => player.status === 'joined')
      .map((player) => player.user_id)
    const invitedPlayerIds = updatedSession.session.players
      .filter((player) => player.status === 'invited')
      .map((player) => player.user_id)

    assert(joinedPlayerIds.includes(friendA.user.id), 'Friend A did not join successfully')
    assert(invitedPlayerIds.includes(friendB.user.id), 'Friend B was not invited successfully from the game sheet')

    await hostPage.screenshot({ path: path.join(outputDir, 'host-game-sheet.png'), fullPage: true, timeout: 60000 })
    await friendAPage.screenshot({
      path: path.join(outputDir, 'friend-a-after-accept.png'),
      fullPage: true,
      timeout: 60000,
    })
    await friendBPage.screenshot({ path: path.join(outputDir, 'friend-b-invite.png'), fullPage: true, timeout: 60000 })

    const result = {
      baseUrl,
      sessionId,
      courtId,
      host: host.user.username,
      invitedInComposer: friendA.user.username,
      invitedFromGameSheet: friendB.user.username,
      friendANotificationTarget: friendANotification.target_path,
      friendBNotificationTarget: friendBNotification.target_path,
      joinedPlayerIds,
      invitedPlayerIds,
      composerMetrics,
      daySheetMetrics,
      hostGameMetrics,
      friendAGameMetricsBeforeAccept,
      friendAGameMetricsAfterAccept,
      friendBGameMetrics,
      screenshots: {
        host: path.join(outputDir, 'host-game-sheet.png'),
        friendA: path.join(outputDir, 'friend-a-after-accept.png'),
        friendB: path.join(outputDir, 'friend-b-invite.png'),
      },
    }

    console.log(JSON.stringify(result, null, 2))
  } finally {
    await Promise.allSettled([
      hostContext.close(),
      friendAContext.close(),
      friendBContext.close(),
      browser.close(),
    ])
  }
}

run().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
