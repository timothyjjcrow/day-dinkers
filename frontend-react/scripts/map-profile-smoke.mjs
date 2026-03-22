import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'

import { chromium, devices } from 'playwright'

const baseUrl = String(process.env.THIRDSHOT_BASE_URL || 'http://127.0.0.1:5001').replace(/\/$/, '')
const outputDir = process.env.THIRDSHOT_SMOKE_OUTPUT || '/tmp/thirdshot-map-profile-smoke'
const device = devices['iPhone 13']
const courtId = Number(process.env.THIRDSHOT_SMOKE_COURT_ID || 1)

async function ensureDir(dirPath) {
  await fs.mkdir(dirPath, { recursive: true })
}

function uniqueLabel(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function toLocalDateTime(date) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

async function request(pathname, { method = 'GET', token, csrf, body } = {}) {
  const headers = {}
  if (token) headers.Authorization = `Bearer ${token}`
  if (csrf) headers['X-CSRF-Token'] = csrf
  if (body !== undefined) headers['Content-Type'] = 'application/json'

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

async function registerUser(label) {
  const username = uniqueLabel(label.replace(/\s+/g, '_'))
  const email = `${username}@example.com`
  const password = 'Playtest123'
  const auth = await request('/api/auth/register', {
    method: 'POST',
    body: {
      username,
      email,
      password,
      name: label,
    },
  })
  return {
    token: auth.token,
    user: auth.user,
    email,
    password,
  }
}

async function seedSession(page, auth) {
  await page.goto(`${baseUrl}/map`, { waitUntil: 'domcontentloaded' })
  await page.evaluate(({ token, user }) => {
    window.localStorage.setItem('thirdshot_token', token)
    window.localStorage.setItem('thirdshot_user', JSON.stringify(user))
  }, { token: auth.token, user: auth.user })
  await page.goto(`${baseUrl}/map`, { waitUntil: 'networkidle' })
}

function assertNoOverflow(metrics, label, { allowDescendantOverflow = false, allowClippedPageBleed = false } = {}) {
  const bodyDelta = metrics.bodyScrollWidth - metrics.bodyClientWidth
  const rootDelta = metrics.rootScrollWidth - metrics.rootClientWidth
  if (allowClippedPageBleed) {
    const maxBleed = typeof allowClippedPageBleed === 'number' ? allowClippedPageBleed : 10
    assert(bodyDelta <= maxBleed, `${label} body overflowed horizontally`)
    assert(rootDelta <= maxBleed, `${label} root overflowed horizontally`)
    assert(['hidden', 'clip'].includes(metrics.bodyOverflowX), `${label} body is not clipping horizontal overflow`)
  } else {
    assert.equal(metrics.bodyClientWidth, metrics.bodyScrollWidth, `${label} body overflowed horizontally`)
    assert.equal(metrics.rootClientWidth, metrics.rootScrollWidth, `${label} root overflowed horizontally`)
  }
  if (!allowDescendantOverflow) {
    assert.equal(metrics.descendantOverflow, false, `${label} had clipped descendants`)
  }
}

function assertDialogNoOverflow(metrics, label) {
  assert.equal(metrics.dialogClientWidth, metrics.dialogScrollWidth, `${label} dialog overflowed horizontally`)
  assert.equal(metrics.descendantOverflow, false, `${label} had clipped descendants`)
}

async function pageOverflowMetrics(page) {
  return page.evaluate(() => {
    const bodyRect = document.body.getBoundingClientRect()
    const descendantOverflow = Array.from(document.body.querySelectorAll('*')).some((element) => {
      const rect = element.getBoundingClientRect()
      return rect.right > bodyRect.right + 0.5
    })

    return {
      bodyClientWidth: document.body.clientWidth,
      bodyScrollWidth: document.body.scrollWidth,
      rootClientWidth: document.documentElement.clientWidth,
      rootScrollWidth: document.documentElement.scrollWidth,
      bodyOverflowX: getComputedStyle(document.body).overflowX,
      rootOverflowX: getComputedStyle(document.documentElement).overflowX,
      descendantOverflow,
    }
  })
}

async function waitForStablePageMetrics(page, label, options = {}) {
  const {
    timeoutMs = 5000,
    allowDescendantOverflow = false,
    allowClippedPageBleed = false,
  } = options
  const started = Date.now()
  let lastError = null

  while (Date.now() - started < timeoutMs) {
    const metrics = await pageOverflowMetrics(page)
    try {
      assertNoOverflow(metrics, label, {
        allowDescendantOverflow,
        allowClippedPageBleed,
      })
      return metrics
    } catch (error) {
      lastError = error
      await page.waitForTimeout(250)
    }
  }

  throw lastError || new Error(`Timed out waiting for stable ${label} metrics`)
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

async function closeDialog(page) {
  await page.getByRole('button', { name: 'Close' }).click()
}

async function visibleMarkerIndexes(page) {
  return page.evaluate(() => {
    const overlayBottom = document.querySelector('.map-overlay-stack')?.getBoundingClientRect().bottom ?? 0
    return Array.from(document.querySelectorAll('.map-court-marker-icon'))
      .map((element, index) => {
        const rect = element.getBoundingClientRect()
        const visible = (
          rect.width > 0
          && rect.height > 0
          && rect.bottom > overlayBottom + 8
          && rect.top < window.innerHeight
          && rect.left < window.innerWidth
          && rect.right > 0
        )
        return visible ? index : null
      })
      .filter((value) => Number.isInteger(value))
  })
}

async function openAnyCourtPopup(page) {
  const courtDetailsButton = page.getByRole('button', { name: /Court Details/i })

  async function popupVisible() {
    return (await courtDetailsButton.count()) > 0
  }

  async function tryVisibleMarkers() {
    const indexes = await visibleMarkerIndexes(page)
    for (const index of indexes) {
      await page.locator('.map-court-marker-icon').nth(index).tap({ force: true })
      await page.waitForTimeout(500)
      if (await page.getByRole('dialog').count()) {
        await page.getByRole('button', { name: 'Close' }).click()
        await page.waitForTimeout(250)
        continue
      }
      if (await popupVisible()) return true
    }
    return false
  }

  if (await tryVisibleMarkers()) return

  const clusterCount = await page.locator('.court-cluster-icon').count()
  for (let index = 0; index < clusterCount; index += 1) {
    await page.locator('.court-cluster-icon').nth(index).tap({ force: true })
    await page.waitForTimeout(900)
    if (await tryVisibleMarkers()) return
  }

  await courtDetailsButton.waitFor({ timeout: 30000 })
}

async function openScheduledDayFlow(page) {
  const scheduledDayIndex = await page.locator('.schedule-day-card').evaluateAll((nodes) => (
    nodes.findIndex((node) => {
      const count = node.querySelector('.schedule-day-card-count')?.textContent || ''
      return /\d/.test(count) && !/open/i.test(count)
    })
  ))

  assert(scheduledDayIndex >= 0, 'Expected at least one scheduled day in the map rail')
  await page.locator('.schedule-day-card').nth(scheduledDayIndex).click()

  const dayDialog = page.getByRole('dialog', { name: 'Day schedule' })
  const gameDialog = page.getByRole('dialog', { name: 'Game' })

  await Promise.race([
    dayDialog.waitFor(),
    gameDialog.waitFor(),
  ])

  if (await gameDialog.isVisible()) {
    return { type: 'game', dialog: gameDialog }
  }

  const dayMetrics = await dialogOverflowMetrics(dayDialog)
  assert.equal(dayMetrics.dialogClientWidth, dayMetrics.dialogScrollWidth, 'Day schedule sheet overflowed horizontally')
  assertNoOverflow(dayMetrics, 'Day schedule sheet')
  await dayDialog.locator('.schedule-card.interactive').first().click()
  await gameDialog.waitFor()
  return { type: 'day', dialog: gameDialog, dayMetrics }
}

async function createScheduledSession(token, sessionTitle) {
  const start = new Date(Date.now() + 90 * 60 * 1000)
  const end = new Date(start.getTime() + 90 * 60 * 1000)
  return authedRequest(token, '/api/sessions', {
    method: 'POST',
    body: {
      court_id: courtId,
      session_type: 'scheduled',
      game_type: 'open',
      skill_level: 'all',
      visibility: 'all',
      max_players: 8,
      notes: sessionTitle,
      start_time: toLocalDateTime(start),
      end_time: toLocalDateTime(end),
    },
  })
}

async function run() {
  await ensureDir(outputDir)

  const user = await registerUser('Surface Tester')
  const sessionTitle = uniqueLabel('Surface Session')
  const createPayload = await createScheduledSession(user.token, sessionTitle)
  const sessionId = createPayload.session.id

  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ ...device })
  const page = await context.newPage()

  try {
    await seedSession(page, user)

    const initialPageMetrics = await waitForStablePageMetrics(page, 'Map page', {
      allowDescendantOverflow: true,
      allowClippedPageBleed: 12,
    })

    assert.equal(await page.locator('.presence-dock').count(), 0, 'Map should not show the live presence dock')
    await page.waitForFunction(
      () => (
        document.querySelectorAll('.map-court-marker-badge.schedule').length >= 1
        || document.querySelectorAll('.court-cluster-icon').length >= 1
      ),
      undefined,
      { timeout: 10000 },
    )
    const scheduleBadgeCount = await page.locator('.map-court-marker-badge.schedule').count()
    const clusterCount = await page.locator('.court-cluster-icon').count()
    assert(scheduleBadgeCount >= 1 || clusterCount >= 1, 'Expected schedule markers or court clusters on the map')

    const scheduleFlow = await openScheduledDayFlow(page)
    const gameDialog = scheduleFlow.dialog
    const gameMetrics = await dialogOverflowMetrics(gameDialog)
    assert.equal(gameMetrics.dialogClientWidth, gameMetrics.dialogScrollWidth, 'Map game sheet overflowed horizontally')
    assertNoOverflow(gameMetrics, 'Map game sheet')
    await page.screenshot({ path: path.join(outputDir, 'map-schedule-sheet.png'), fullPage: true })
    await closeDialog(page)

    await openAnyCourtPopup(page)
    assert.equal(await page.locator('.map-overlay-stack').count(), 0, 'Map overlays should hide while a popup is open')
    await page.screenshot({ path: path.join(outputDir, 'map-popup.png'), fullPage: true })
    await page.getByRole('button', { name: /Court Details/i }).click()
    await page.waitForURL(/\/courts\/\d+/)
    const courtMetrics = await waitForStablePageMetrics(page, 'Court page', {
      allowDescendantOverflow: true,
      allowClippedPageBleed: 12,
    })

    await page.goto(`${baseUrl}/profile`, { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: /Edit Profile/i }).waitFor()
    await page.getByText(new RegExp(user.user.name, 'i')).waitFor()
    assert.equal(await page.locator('.presence-dock').count(), 0, 'Profile should not show the live presence dock')
    const profileMetrics = await waitForStablePageMetrics(page, 'Profile page', {
      allowDescendantOverflow: true,
      allowClippedPageBleed: 80,
    })
    await page.getByRole('button', { name: /Leaderboard/i }).click()
    await page.getByText(/leaderboard/i).first().waitFor()
    await page.getByRole('button', { name: /Edit Profile/i }).click()
    const editDialog = page.getByRole('dialog', { name: 'Edit Profile' })
    await editDialog.waitFor()
    const editMetrics = await dialogOverflowMetrics(editDialog)
    assertDialogNoOverflow(editMetrics, 'Edit profile sheet')
    await editDialog.getByRole('button', { name: 'Close' }).click()
    await editDialog.waitFor({ state: 'hidden' })
    await page.getByRole('button', { name: /Edit Profile/i }).click()
    await editDialog.waitFor()
    await editDialog.getByLabel('Bio').fill('Compact ladder grinder who likes easy match flow.')
    await editDialog.getByLabel('Play style').fill('Fast doubles')
    await editDialog.getByLabel('Preferred times').fill('Weeknights')
    await editDialog.getByRole('button', { name: /Save Profile/i }).click()
    await page.getByText(/Compact ladder grinder/i).waitFor()
    await page.screenshot({ path: path.join(outputDir, 'profile.png'), fullPage: true })

    console.log(JSON.stringify({
      baseUrl,
      sessionId,
      courtId,
      user: user.user.username,
      metrics: {
        initialPageMetrics,
        ...(scheduleFlow.type === 'day' ? { dayMetrics: scheduleFlow.dayMetrics } : {}),
        gameMetrics,
        courtMetrics,
        profileMetrics,
        editMetrics,
      },
      screenshots: {
        mapScheduleSheet: path.join(outputDir, 'map-schedule-sheet.png'),
        mapPopup: path.join(outputDir, 'map-popup.png'),
        profile: path.join(outputDir, 'profile.png'),
      },
    }, null, 2))
  } finally {
    await Promise.allSettled([
      context.close(),
      browser.close(),
    ])
  }
}

run().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
