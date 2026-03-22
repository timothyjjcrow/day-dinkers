import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'

import { chromium, devices } from 'playwright'

const baseUrl = String(process.env.THIRDSHOT_BASE_URL || 'http://127.0.0.1:5001').replace(/\/$/, '')
const outputDir = process.env.THIRDSHOT_SMOKE_OUTPUT || '/tmp/thirdshot-ranked-smoke'
const device = devices['iPhone 13']
const fixedCourtId = process.env.THIRDSHOT_SMOKE_COURT_ID
  ? Number(process.env.THIRDSHOT_SMOKE_COURT_ID)
  : null
const localDbPath = process.env.THIRDSHOT_SMOKE_DB_PATH || path.resolve(process.cwd(), '..', 'pickleball_dev.db')
const smokeCourtSeed = {
  address: '1011 Waterfront Dr',
  city: 'Eureka',
  state: 'CA',
  county_slug: 'humboldt',
  latitude: 40.806492,
  longitude: -124.159685,
  num_courts: 4,
  lighted: true,
  surface_type: 'outdoor hardcourt',
}
const createdSmokeCourtIds = new Set()
const staleSmokeCourtPatterns = [
  'Debug Court_%',
  'PW Court_%',
  'QueueCtxCourt_%',
  'QueueUICourt_%',
  'Ranked Check In Court_%',
  'Ranked Challenge Court_%',
  'Ranked Queue Court_%',
]

async function ensureDir(dirPath) {
  await fs.mkdir(dirPath, { recursive: true })
}

function uniqueLabel(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
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

async function createSmokeCourt(token, label, offsetIndex = 0) {
  if (fixedCourtId) return fixedCourtId
  const latitudeOffset = 0.00018 * offsetIndex
  const longitudeOffset = 0.00014 * offsetIndex
  const payload = await authedRequest(token, '/api/courts', {
    method: 'POST',
    body: {
      ...smokeCourtSeed,
      name: uniqueLabel(label),
      latitude: smokeCourtSeed.latitude + latitudeOffset,
      longitude: smokeCourtSeed.longitude - longitudeOffset,
    },
  })
  createdSmokeCourtIds.add(payload.court.id)
  return payload.court.id
}

function cleanupSqlForCourtIds(courtIds) {
  const idList = courtIds.join(',')
  return `
    PRAGMA foreign_keys = OFF;
    DELETE FROM notification
    WHERE (notif_type IN ('session_invite', 'session_join', 'session_cancelled')
      AND reference_id IN (SELECT id FROM play_session WHERE court_id IN (${idList})))
      OR (notif_type IN ('ranked_challenge_invite', 'ranked_challenge_ready')
      AND reference_id IN (SELECT id FROM ranked_lobby WHERE court_id IN (${idList})))
      OR (notif_type IN ('match_confirm', 'match_result')
      AND reference_id IN (SELECT id FROM match WHERE court_id IN (${idList})));
    DELETE FROM message_read_receipt
    WHERE thread_type = 'session'
      AND thread_ref_id IN (SELECT id FROM play_session WHERE court_id IN (${idList}));
    DELETE FROM game_player
    WHERE game_id IN (SELECT id FROM game WHERE court_id IN (${idList}));
    DELETE FROM game WHERE court_id IN (${idList});
    DELETE FROM tournament_participant
    WHERE tournament_id IN (SELECT id FROM tournament WHERE court_id IN (${idList}));
    DELETE FROM tournament_result
    WHERE tournament_id IN (SELECT id FROM tournament WHERE court_id IN (${idList}))
      OR court_id IN (${idList});
    DELETE FROM tournament WHERE court_id IN (${idList});
    DELETE FROM ranked_lobby_player
    WHERE lobby_id IN (SELECT id FROM ranked_lobby WHERE court_id IN (${idList}));
    DELETE FROM ranked_lobby WHERE court_id IN (${idList});
    DELETE FROM match_player
    WHERE match_id IN (SELECT id FROM match WHERE court_id IN (${idList}));
    DELETE FROM match WHERE court_id IN (${idList});
    DELETE FROM ranked_queue WHERE court_id IN (${idList});
    DELETE FROM recurring_session_series_item
    WHERE session_id IN (SELECT id FROM play_session WHERE court_id IN (${idList}));
    DELETE FROM play_session_player
    WHERE session_id IN (SELECT id FROM play_session WHERE court_id IN (${idList}));
    DELETE FROM message
    WHERE session_id IN (SELECT id FROM play_session WHERE court_id IN (${idList}))
      OR court_id IN (${idList});
    DELETE FROM play_session WHERE court_id IN (${idList});
    DELETE FROM check_in WHERE court_id IN (${idList});
    DELETE FROM activity_log WHERE court_id IN (${idList});
    DELETE FROM court_report WHERE court_id IN (${idList});
    DELETE FROM court_community_info WHERE court_id IN (${idList});
    DELETE FROM court_event WHERE court_id IN (${idList});
    DELETE FROM court_image WHERE court_id IN (${idList});
    DELETE FROM court_update_submission WHERE court_id IN (${idList});
    DELETE FROM court WHERE id IN (${idList});
    PRAGMA foreign_keys = ON;
  `
}

function cleanupCourtIds(courtIds) {
  if (!courtIds.length) return
  execFileSync('sqlite3', [localDbPath, cleanupSqlForCourtIds(courtIds)], { stdio: 'pipe' })
}

async function cleanupCreatedSmokeCourts() {
  if (fixedCourtId || !createdSmokeCourtIds.size) return
  if (!/127\.0\.0\.1|localhost/.test(baseUrl)) return

  try {
    await fs.access(localDbPath)
  } catch {
    return
  }

  const courtIds = [...createdSmokeCourtIds]
  if (!courtIds.length) return
  try {
    cleanupCourtIds(courtIds)
  } catch (error) {
    console.warn('Unable to cleanup temporary smoke courts automatically.', error instanceof Error ? error.message : error)
  }
}

async function cleanupStaleSmokeCourts() {
  if (fixedCourtId) return
  if (!/127\.0\.0\.1|localhost/.test(baseUrl)) return

  try {
    await fs.access(localDbPath)
  } catch {
    return
  }

  const query = `
    SELECT id FROM court
    WHERE ${staleSmokeCourtPatterns.map((pattern) => `name LIKE '${pattern}'`).join(' OR ')};
  `

  try {
    const stdout = execFileSync('sqlite3', [localDbPath, query], { encoding: 'utf8', stdio: 'pipe' })
    const ids = stdout
      .split('\n')
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isInteger(value) && value > 0)

    if (!ids.length) return
    cleanupCourtIds(ids)
  } catch (error) {
    console.warn('Unable to cleanup stale smoke courts automatically.', error instanceof Error ? error.message : error)
  }
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

async function makeFriends(host, other) {
  await authedRequest(host.token, '/api/auth/friends/request', {
    method: 'POST',
    body: { friend_id: other.user.id },
  })
  const pending = await authedRequest(other.token, '/api/auth/friends/pending')
  const requestRow = pending.requests.find((row) => row.user?.id === host.user.id)
  assert(requestRow, `Expected pending request for ${other.user.username}`)
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

async function openCourt(page, id) {
  await page.goto(`${baseUrl}/courts/${id}`, { waitUntil: 'networkidle' })
}

async function waitForButton(page, name, { reload = true, timeoutMs = 15000 } = {}) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const button = page.getByRole('button', { name }).first()
    try {
      if (await button.isVisible()) return button
    } catch {
      // Keep polling.
    }
    if (reload) {
      await page.reload({ waitUntil: 'networkidle' })
    }
    await page.waitForTimeout(400)
  }
  throw new Error(`Timed out waiting for button ${String(name)}`)
}

async function checkIn(page, id) {
  await openCourt(page, id)
  const button = await waitForButton(page, /Check In|Move Here/i, { reload: false, timeoutMs: 5000 })
  await button.click()
  await waitForButton(page, /Want Game|Ready Now|Schedule Later|Join Queue/i, { reload: true })
}

async function checkInViaApi(token, courtId) {
  await authedRequest(token, '/api/presence/checkin', {
    method: 'POST',
    body: { court_id: courtId },
  })
}

async function joinQueue(page, id) {
  await openCourt(page, id)
  const joinButton = await waitForButton(page, /^Join Queue$/i)
  const joinResponsePromise = page.waitForResponse((response) => (
    response.url() === `${baseUrl}/api/ranked/queue/join`
      && response.request().method() === 'POST'
  ))
  await joinButton.click()
  await joinResponsePromise
  await openCourt(page, id)
  try {
    await waitForButton(page, /Leave Queue|Start Next Game/i, { reload: false, timeoutMs: 5000 })
  } catch {
    await page.getByText(/In Queue/i).waitFor()
  }
}

async function joinQueueViaApi(token, courtId, matchType = 'doubles') {
  await authedRequest(token, '/api/ranked/queue/join', {
    method: 'POST',
    body: {
      court_id: courtId,
      match_type: matchType,
    },
  })
}

function dialogOverflowMetrics(locator) {
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

async function waitForCompletedHistory(token, userId, timeoutMs = 30000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const history = await authedRequest(token, `/api/ranked/history?user_id=${userId}&limit=5`)
    if (history.matches.length && history.matches[0].status === 'completed') {
      return history
    }
    await new Promise((resolve) => setTimeout(resolve, 400))
  }
  throw new Error('Timed out waiting for completed ranked history')
}

async function waitForMatchStatus(matchId, expectedStatus = 'completed', timeoutMs = 30000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const payload = await request(`/api/ranked/match/${matchId}`)
    if (payload.match?.status === expectedStatus) {
      return payload.match
    }
    await new Promise((resolve) => setTimeout(resolve, 350))
  }
  throw new Error(`Timed out waiting for match ${matchId} to reach ${expectedStatus}`)
}

async function waitForGamesPlayed(token, minimumGames = 1, timeoutMs = 30000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const profile = await authedRequest(token, '/api/auth/profile')
    if ((profile.user.games_played || 0) >= minimumGames) {
      return profile
    }
    await new Promise((resolve) => setTimeout(resolve, 350))
  }
  throw new Error(`Timed out waiting for profile to reach ${minimumGames} saved games`)
}

async function waitForPresenceAtCourt(token, expectedCourtId, timeoutMs = 12000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const status = await authedRequest(token, '/api/presence/status')
    if (status.checked_in && status.court_id === expectedCourtId) {
      return status
    }
    await new Promise((resolve) => setTimeout(resolve, 300))
  }
  throw new Error(`Timed out waiting for presence at court ${expectedCourtId}`)
}

async function waitForActiveMatchAtCourt(token, expectedCourtId, timeoutMs = 12000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const activeMatches = await authedRequest(token, `/api/ranked/active/${expectedCourtId}`)
    if (activeMatches.matches.some((match) => match.status === 'in_progress' || match.status === 'pending_confirmation')) {
      return activeMatches
    }
    await new Promise((resolve) => setTimeout(resolve, 350))
  }
  throw new Error(`Timed out waiting for an active ranked match at court ${expectedCourtId}`)
}

async function waitForChallengeablePlayer(token, expectedCourtId, otherUserId, timeoutMs = 12000) {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const hub = await authedRequest(token, `/api/courts/${expectedCourtId}/hub`)
    const players = hub?.ranked?.challengeable_players || []
    if (players.some((player) => Number(player.user_id ?? player.id) === Number(otherUserId))) {
      return hub
    }
    await new Promise((resolve) => setTimeout(resolve, 350))
  }
  throw new Error(`Timed out waiting for challengeable player ${otherUserId} at court ${expectedCourtId}`)
}

async function confirmMatchWithUiOrApi(page, token, courtId, matchId) {
  await openCourt(page, courtId)
  try {
    const confirmButton = await waitForButton(page, /Confirm Score/i, { timeoutMs: 5000 })
    const confirmResponsePromise = page.waitForResponse((response) => (
      response.url() === `${baseUrl}/api/ranked/match/${matchId}/confirm`
        && response.request().method() === 'POST'
    ))
    await confirmButton.click()
    await confirmResponsePromise
    return 'ui'
  } catch {
    await authedRequest(token, `/api/ranked/match/${matchId}/confirm`, { method: 'POST', body: {} })
    return 'api'
  }
}

async function runCheckInUiSanity(browser) {
  const user = await registerUser('Check In UI')
  const smokeCourtId = await createSmokeCourt(user.token, 'Ranked Check In Court', 0)
  const context = await browser.newContext({ ...device })
  const page = await context.newPage()

  try {
    await seedSession(page, user)
    await checkIn(page, smokeCourtId)
    await waitForPresenceAtCourt(user.token, smokeCourtId)
    await openCourt(page, smokeCourtId)
    await waitForButton(page, /Want Game|Ready Now|Schedule Later|Join Queue/i, { reload: true })
    return { user: user.user.username, courtId: smokeCourtId }
  } finally {
    await context.close()
  }
}

async function runChallengeFlow(browser) {
  const host = await registerUser('Ranked Host')
  const friend = await registerUser('Ranked Friend')
  await makeFriends(host, friend)
  const challengeCourtId = await createSmokeCourt(host.token, 'Ranked Challenge Court', 1)

  const hostContext = await browser.newContext({ ...device })
  const friendContext = await browser.newContext({ ...device })
  const hostPage = await hostContext.newPage()
  const friendPage = await friendContext.newPage()

  try {
    await Promise.all([
      seedSession(hostPage, host),
      seedSession(friendPage, friend),
    ])

    await Promise.all([
      checkInViaApi(host.token, challengeCourtId),
      checkInViaApi(friend.token, challengeCourtId),
    ])
    await Promise.all([
      openCourt(hostPage, challengeCourtId),
      openCourt(friendPage, challengeCourtId),
    ])
    await waitForButton(hostPage, /Want Game|Ready Now|Schedule Later|Join Queue/i, { reload: true })
    await waitForButton(friendPage, /Want Game|Ready Now|Schedule Later|Join Queue/i, { reload: true })
    await Promise.all([
      waitForPresenceAtCourt(host.token, challengeCourtId),
      waitForPresenceAtCourt(friend.token, challengeCourtId),
    ])
    await waitForChallengeablePlayer(host.token, challengeCourtId, friend.user.id)

    await openCourt(hostPage, challengeCourtId)
    const challengeButton = await waitForButton(hostPage, new RegExp(friend.user.name, 'i'))
    await challengeButton.click()

    await openCourt(friendPage, challengeCourtId)
    const acceptButton = await waitForButton(friendPage, /^Accept$/i)
    await acceptButton.click()

    await openCourt(hostPage, challengeCourtId)
    const startButton = await waitForButton(hostPage, /Start Ready Game/i)
    await startButton.click()

    await openCourt(hostPage, challengeCourtId)
    const enterScoreButton = await waitForButton(hostPage, /Enter Score/i)
    await enterScoreButton.click()
    const scoreDialog = hostPage.getByRole('dialog', { name: 'Post Final Score' })
    await scoreDialog.waitFor()
    const scoreMetrics = await dialogOverflowMetrics(scoreDialog)
    assertNoOverflow(scoreMetrics, 'Ranked score sheet')
    await scoreDialog.getByLabel('Team 1 score').fill('11')
    await scoreDialog.getByLabel('Team 2 score').fill('8')
    await scoreDialog.getByRole('button', { name: /Save Score/i }).click()

    await openCourt(friendPage, challengeCourtId)
    const confirmButton = await waitForButton(friendPage, /Confirm Score/i)
    await confirmButton.click()

    const history = await waitForCompletedHistory(host.token, host.user.id)
    assert(history.matches.length >= 1, 'Expected ranked history to include completed match')
    assert.equal(history.matches[0].status, 'completed')
    const friendHistory = await waitForCompletedHistory(friend.token, friend.user.id)
    assert(friendHistory.matches.some((match) => match.id === history.matches[0].id), 'Friend history did not save the same ranked match')

    const leaderboard = await authedRequest(host.token, `/api/ranked/leaderboard?court_id=${challengeCourtId}&limit=100`)
    const leaderboardIds = new Set(leaderboard.leaderboard.map((entry) => entry.user_id))
    assert(leaderboardIds.has(host.user.id), 'Host missing from leaderboard after ranked result')

    await hostPage.goto(`${baseUrl}/profile`, { waitUntil: 'networkidle' })
    await hostPage.getByText(/Ranked form/i).waitFor()
    await hostPage.getByRole('button', { name: /Recent/i }).click()
    await hostPage.getByText(/11 - 8/i).waitFor()

    await Promise.all([
      hostPage.screenshot({ path: path.join(outputDir, 'ranked-host-profile.png'), fullPage: true }),
      friendPage.screenshot({ path: path.join(outputDir, 'ranked-friend-confirmed.png'), fullPage: true }),
    ])

    const profile = await authedRequest(host.token, '/api/auth/profile')
    const friendProfile = await authedRequest(friend.token, '/api/auth/profile')
    assert((profile.user.games_played || 0) >= 1, 'Host profile did not record the ranked result')
    assert((friendProfile.user.games_played || 0) >= 1, 'Friend profile did not record the ranked result')

    return {
      host: {
        id: host.user.id,
        username: host.user.username,
        name: host.user.name,
      },
      friend: {
        id: friend.user.id,
        username: friend.user.username,
        name: friend.user.name,
      },
      courtId: challengeCourtId,
      profileGamesPlayed: profile.user.games_played,
      leaderboardCount: leaderboard.leaderboard.length,
      latestMatchId: history.matches[0].id,
      scoreMetrics,
      screenshots: {
        hostProfile: path.join(outputDir, 'ranked-host-profile.png'),
        friendConfirmed: path.join(outputDir, 'ranked-friend-confirmed.png'),
      },
    }
  } finally {
    await Promise.allSettled([
      hostContext.close(),
      friendContext.close(),
    ])
  }
}

async function runQueueFlow(browser) {
  const players = await Promise.all([
    registerUser('Queue One'),
    registerUser('Queue Two'),
    registerUser('Queue Three'),
    registerUser('Queue Four'),
  ])
  const queueCourtId = await createSmokeCourt(players[0].token, 'Ranked Queue Court', 2)
  const contexts = await Promise.all(players.map(() => browser.newContext({ ...device })))
  const pages = await Promise.all(contexts.map((context) => context.newPage()))

  try {
    await Promise.all(pages.map((page, index) => seedSession(page, players[index])))
    await Promise.all(players.map((player) => checkInViaApi(player.token, queueCourtId)))
    await Promise.all(players.map((player) => waitForPresenceAtCourt(player.token, queueCourtId)))
    await Promise.all(pages.map((page) => openCourt(page, queueCourtId)))
    await joinQueue(pages[0], queueCourtId)
    await Promise.all(players.slice(1).map((player) => joinQueueViaApi(player.token, queueCourtId)))

    await openCourt(pages[0], queueCourtId)
    await waitForButton(pages[0], /Start Next Game|Leave Queue/i)

    const queueLobby = await authedRequest(players[0].token, '/api/ranked/lobby/queue', {
      method: 'POST',
      body: {
        court_id: queueCourtId,
        match_type: 'doubles',
        team1: [players[0].user.id, players[1].user.id],
        team2: [players[2].user.id, players[3].user.id],
        start_immediately: true,
      },
    })
    const queueMatch = queueLobby.match
    assert(queueMatch?.id, 'Queue flow did not create a deterministic match for the queued players')

    await openCourt(pages[0], queueCourtId)
    const enterScoreButton = await waitForButton(pages[0], /Enter Score/i)
    await enterScoreButton.click()
    const scoreDialog = pages[0].getByRole('dialog', { name: 'Post Final Score' })
    await scoreDialog.waitFor()
    const scoreMetrics = await dialogOverflowMetrics(scoreDialog)
    assertNoOverflow(scoreMetrics, 'Queue score sheet')
    await scoreDialog.getByLabel('Team 1').fill('11')
    await scoreDialog.getByLabel('Team 2').fill('7')
    await scoreDialog.getByRole('button', { name: /Save Score/i }).click()

    const confirmationModes = []
    for (const [index, page] of pages.slice(1).entries()) {
      confirmationModes.push(
        await confirmMatchWithUiOrApi(page, players[index + 1].token, queueCourtId, queueMatch.id),
      )
    }

    const completedMatch = await waitForMatchStatus(queueMatch.id, 'completed')
    assert.equal(completedMatch.id, queueMatch.id)

    const playerProfiles = await Promise.all(players.map((player) => waitForGamesPlayed(player.token, 1)))
    for (const profile of playerProfiles) {
      assert((profile.user.games_played || 0) >= 1, 'Queue player profile did not record a saved ranked game')
    }

    await pages[0].screenshot({ path: path.join(outputDir, 'queue-completed.png'), fullPage: true })

    return {
      queueUsers: players.map((player) => player.user.username),
      courtId: queueCourtId,
      activeMatchCount: 1,
      latestQueueMatchId: queueMatch.id,
      savedGamesPerPlayer: playerProfiles.map((profile) => profile.user.games_played || 0),
      confirmationModes,
      screenshot: path.join(outputDir, 'queue-completed.png'),
    }
  } finally {
    await Promise.allSettled(contexts.map((context) => context.close()))
  }
}

async function run() {
  await ensureDir(outputDir)
  await cleanupStaleSmokeCourts()
  const browser = await chromium.launch({ headless: true })

  try {
    const checkInUi = await runCheckInUiSanity(browser)
    const challenge = await runChallengeFlow(browser)
    const queue = await runQueueFlow(browser)

    console.log(JSON.stringify({
      baseUrl,
      fixedCourtId,
      checkInUi,
      challenge,
      queue,
    }, null, 2))
  } finally {
    await browser.close()
    await cleanupCreatedSmokeCourts()
  }
}

run().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
