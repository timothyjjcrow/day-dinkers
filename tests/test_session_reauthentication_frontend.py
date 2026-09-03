"""Expired credentials preserve the active interface and resume one request."""

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
INDEX = (ROOT / 'public' / 'index.html').read_text()
STYLES = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(start: str, end: str) -> str:
    offset = APP.index(start)
    return APP[offset:APP.index(end, offset)]


def test_protected_401_reauthenticates_in_place_and_replays_once():
    api = section('async function api(', '// Password and MFA mutations')
    assert "data?.error === 'authentication_required'" in api
    assert "await requireSessionReauthentication({" in api
    assert "return api(path, { ...options, _reauthAttempted: true });" in api
    assert "if (_reauthAttempted)" in api
    assert "data?.error === 'authentication_required'" in api
    assert "logout({" not in api
    assert "_reauthAttempted = false" in api
    assert "...requestOptions" in api


def test_only_public_credential_entry_routes_bypass_reauthentication():
    api = section('async function api(', '// Password and MFA mutations')
    partition = section(
        'const PUBLIC_CREDENTIAL_AUTH_PATHS',
        'async function api(',
    )
    public_paths = {
        '/auth/register',
        '/auth/login',
        '/auth/forgot-password',
        '/auth/reset-password',
        '/auth/verify-email',
        '/auth/confirm-email-change',
    }
    for path in public_paths:
        assert f"'{path}'" in partition
    for protected_path in (
        '/auth/verify-email/request',
        '/auth/change-email',
        '/auth/change-password',
        '/auth/mfa/setup',
        '/auth/mfa/enable',
        '/auth/mfa/disable',
        '/auth/sessions/revoke-others',
    ):
        assert f"'{protected_path}'" not in partition
    assert "!PUBLIC_CREDENTIAL_AUTH_PATHS.has(String(path).split('?', 1)[0])" in api
    assert "!path.startsWith('/auth')" not in api


def test_reauthentication_is_deduplicated_and_account_pinned():
    reauth = section(
        'function authenticatedTokenAccountId(token)',
        '// A single, branded decision sheet',
    )
    assert 'let sessionReauthPromise = null;' in APP
    assert 'if (sessionReauthPromise)' in reauth
    assert 'return sessionReauthPromise;' in reauth
    assert 'authenticatedTokenAccountId(requestToken)' in reauth
    assert 'authenticatedAccountId !== accountId' in reauth
    assert 'persistReplacementToken(data, { preserveSessionEpoch: !!accountId })' in reauth
    assert 'applyMe(data);' in reauth
    assert 'readonly aria-readonly="true"' not in reauth
    assert 'If you recently changed it, enter the new address.' in reauth


def test_reauthentication_keeps_the_underlying_dom_and_supports_mfa():
    reauth = section(
        'function requireSessionReauthentication(',
        '// A single, branded decision sheet',
    )
    assert 'this screen and everything you entered are still here' in reauth
    assert 'id="session-reauth-mfa-field"' in reauth
    assert 'inputmode="text" autocapitalize="none" spellcheck="false"' in reauth
    assert "error.code === 'mfa_required'" in reauth
    assert "body.mfa_code = mfa.value.trim()" in reauth
    assert 'modal._dismissBlocked = () => true;' in reauth
    assert 'Sign out instead' in reauth
    assert 'logout({ preserveEmail: true, preserveRoute: true });' in reauth
    assert 'dismissAllModals' not in reauth
    assert '.session-reauth-backdrop' in STYLES
    assert '.session-reauth-form' in STYLES
    assert 'id="auth-mfa-code"' in INDEX
    assert 'inputmode="text" autocapitalize="none" spellcheck="false"' in INDEX


def test_concurrent_old_responses_only_replay_for_the_same_account():
    api = section('async function api(', '// Password and MFA mutations')
    assert 'const requestTokenRevision = authTokenRevision;' in api
    assert 'authTokenRevision === requestTokenRevision' in api
    assert 'state.token && state.token !== requestToken' in api
    assert 'authSessionEpoch === requestSessionEpoch' in api
    assert "persistReplacementToken(data, { preserveSessionEpoch: !!accountId })" in APP
    assert "requestOptions.signal?.aborted" in api


def test_sliding_tokens_do_not_invalidate_same_account_response_owners():
    helpers = section(
        'function captureAuthenticatedSessionOwner()',
        'function stopThreadPolling()',
    )
    assert "{ epoch: authSessionEpoch, userId }" in helpers
    assert "authSessionEpoch === owner.epoch" in helpers
    assert "safePositiveId(state.me && state.me.id) === owner.userId" in helpers

    refresh_me = section('async function refreshMe()', '// ---------- Tabs ----------')
    assert 'captureAuthenticatedSessionOwner()' in refresh_me
    assert 'authenticatedSessionOwnerIsCurrent(requestOwner)' in refresh_me
    assert 'state.token !== requestToken' not in refresh_me

    looking = section('function lookingBannerContext(', 'function hideSearchSuggest(')
    assert 'owner?.epoch' in looking
    assert 'owner?.userId' in looking
    assert 'authenticatedSessionOwnerIsCurrent(requestOwner)' in looking
    assert 'state.token === token' not in looking

    main = section('async function showMain()', 'function slotForNow(')
    assert 'authenticatedSessionOwnerIsCurrent(pingOwner)' in main
    assert 'state.token !== pingToken' not in main


def test_api_reauthentication_runtime_replays_once_and_partitions_public_auth():
    source = section(
        'const PUBLIC_CREDENTIAL_AUTH_PATHS',
        '// Password and MFA mutations',
    )
    harness = r"""
      const assert = require('node:assert/strict');
      const source = __SOURCE__;

      function response(status, data, headers = {}) {
        const normalized = Object.fromEntries(
          Object.entries(headers).map(([key, value]) => [key.toLowerCase(), value]),
        );
        return {
          status,
          ok: status >= 200 && status < 300,
          headers: { get: (name) => normalized[String(name).toLowerCase()] || null },
          json: async () => typeof data === 'function' ? data() : data,
        };
      }

      function runtime(fetchImpl, requireImpl, token = 'old-token') {
        const state = { token, me: { id: 7 }, networkFailureCount: 0 };
        const stored = new Map([['pp_token', token]]);
        const factory = new Function(
          'state', 'fetch', 'requireSessionReauthentication', 'stored',
          `
            let authSessionEpoch = 0;
            let authTokenRevision = 0;
            const navigator = { onLine: true };
            const localStorage = {
              setItem: (key, value) => stored.set(key, String(value)),
              getItem: (key) => stored.get(key) || null,
            };
            const setConnectionState = () => {};
            const safePositiveId = (value) => Number(value) > 0 ? Number(value) : null;
            const authenticatedTokenAccountId = (value) => value ? 7 : null;
            const humanError = (code) => code;
            const sessionAuthenticationError = (message, code) => {
              const error = new Error(message);
              error.code = code;
              error.isAuthExpired = true;
              return error;
            };
            ${source}
            return {
              api,
              switchAccount: (token) => {
                authSessionEpoch += 1;
                authTokenRevision += 1;
                state.token = token;
              },
            };
          `,
        );
        return { state, stored, ...factory(state, fetchImpl, requireImpl, stored) };
      }

      (async () => {
        const body = JSON.stringify({ current_password: 'old', new_password: 'new-value' });
        const protectedCalls = [];
        let protectedRuntime;
        let reauthCount = 0;
        protectedRuntime = runtime(async (url, options) => {
          protectedCalls.push({ url, auth: options.headers.Authorization, body: options.body });
          return protectedCalls.length === 1
            ? response(401, { error: 'authentication_required' })
            : response(200, { ok: true });
        }, async ({ expectedAccountId }) => {
          reauthCount += 1;
          assert.equal(expectedAccountId, 7);
          protectedRuntime.state.token = 'fresh-token';
        });
        assert.deepEqual(
          await protectedRuntime.api('/auth/change-password', { method: 'POST', body }),
          { ok: true },
        );
        assert.equal(reauthCount, 1);
        assert.equal(protectedCalls.length, 2);
        assert.deepEqual(protectedCalls.map((call) => call.auth), [
          'Bearer old-token', 'Bearer fresh-token',
        ]);
        assert.deepEqual(protectedCalls.map((call) => call.body), [body, body]);

        for (const path of [
          '/auth/register', '/auth/login', '/auth/forgot-password',
          '/auth/reset-password', '/auth/verify-email', '/auth/confirm-email-change',
        ]) {
          let publicReauthCount = 0;
          const publicRuntime = runtime(
            async () => response(401, { error: 'authentication_required' }),
            async () => { publicReauthCount += 1; },
          );
          await assert.rejects(
            publicRuntime.api(`${path}?source=test`, { method: 'POST', body: '{}' }),
            (error) => error.code === 'authentication_required',
          );
          assert.equal(publicReauthCount, 0, `${path} must not recurse into reauth`);
        }

        const concurrentCalls = [];
        let concurrentRuntime;
        let releaseGate;
        let gate = null;
        let gatesCreated = 0;
        const requireShared = () => {
          if (!gate) {
            gatesCreated += 1;
            gate = new Promise((resolve) => {
              releaseGate = () => {
                concurrentRuntime.state.token = 'fresh-token';
                resolve();
              };
            });
          }
          return gate;
        };
        concurrentRuntime = runtime(async (_url, options) => {
          concurrentCalls.push({ auth: options.headers.Authorization, body: options.body });
          return options.headers.Authorization === 'Bearer old-token'
            ? response(401, { error: 'authentication_required' })
            : response(200, { ok: true });
        }, requireShared);
        const first = concurrentRuntime.api('/games/1/join', { method: 'POST', body: '{"slot":1}' });
        const second = concurrentRuntime.api('/games/2/join', { method: 'POST', body: '{"slot":2}' });
        for (let index = 0; index < 10 && !releaseGate; index += 1) await Promise.resolve();
        assert.equal(typeof releaseGate, 'function');
        releaseGate();
        assert.deepEqual(await Promise.all([first, second]), [{ ok: true }, { ok: true }]);
        assert.equal(gatesCreated, 1);
        assert.equal(concurrentCalls.length, 4);
        assert.deepEqual(
          concurrentCalls.map((call) => call.body).sort(),
          ['{"slot":1}', '{"slot":1}', '{"slot":2}', '{"slot":2}'],
        );

        let rejectedCalls = 0;
        let rejectedRuntime;
        rejectedRuntime = runtime(async () => {
          rejectedCalls += 1;
          return response(401, { error: 'authentication_required' });
        }, async () => { rejectedRuntime.state.token = 'fresh-token'; });
        await assert.rejects(
          rejectedRuntime.api('/games/9/join', { method: 'POST', body: '{}' }),
          (error) => error.code === 'session_restore_rejected',
        );
        assert.equal(rejectedCalls, 2, 'a rejected replay must never become a loop');

        let rotationCalls = 0;
        let rotationPrompts = 0;
        let rotationRuntime;
        rotationRuntime = runtime(async (_url, options) => {
          rotationCalls += 1;
          if (rotationCalls === 1) {
            rotationRuntime.state.token = 'slid-token';
            return response(401, { error: 'authentication_required' });
          }
          assert.equal(options.headers.Authorization, 'Bearer slid-token');
          return response(200, { recovered: true });
        }, async () => { rotationPrompts += 1; });
        assert.deepEqual(await rotationRuntime.api('/me/stats'), { recovered: true });
        assert.equal(rotationCalls, 2);
        assert.equal(rotationPrompts, 0, 'a concurrent sliding-token win should avoid a password prompt');

        const signal = { aborted: false };
        let abortCalls = 0;
        let abortRuntime;
        abortRuntime = runtime(async () => {
          abortCalls += 1;
          return response(401, { error: 'authentication_required' });
        }, async () => {
          abortRuntime.state.token = 'fresh-token';
          signal.aborted = true;
        });
        await assert.rejects(
          abortRuntime.api('/games/7/join', { method: 'POST', body: '{}', signal }),
          (error) => error.code === 'request_cancelled',
        );
        assert.equal(abortCalls, 1, 'a cancelled owner must not replay after reauth');

        const alreadyAbortedSignal = { aborted: true };
        let alreadyAbortedCalls = 0;
        let alreadyAbortedPrompts = 0;
        const alreadyAbortedRuntime = runtime(async () => {
          alreadyAbortedCalls += 1;
          return response(401, { error: 'authentication_required' });
        }, async () => { alreadyAbortedPrompts += 1; });
        await assert.rejects(
          alreadyAbortedRuntime.api('/players/looking', { signal: alreadyAbortedSignal }),
          (error) => error.code === 'request_cancelled',
        );
        assert.equal(alreadyAbortedCalls, 1);
        assert.equal(alreadyAbortedPrompts, 0, 'a retired owner must not open reauth');

        let accountRaceCalls = 0;
        let accountRacePrompts = 0;
        let accountRaceRuntime;
        accountRaceRuntime = runtime(async () => {
          accountRaceCalls += 1;
          return response(401, () => {
            // Reproduce logout + a different login while the old response
            // body is yielding, after api()'s header-time epoch assertion.
            accountRaceRuntime.switchAccount('different-account-token');
            return { error: 'authentication_required' };
          });
        }, async () => { accountRacePrompts += 1; });
        await assert.rejects(
          accountRaceRuntime.api('/games/11/join', { method: 'POST', body: '{"slot":4}' }),
          (error) => error.code === 'stale_session' && error.isStaleSession === true,
        );
        assert.equal(accountRaceCalls, 1, 'an earlier account request must never replay');
        assert.equal(accountRacePrompts, 0, 'an earlier account request must not open reauth');

        let gateRaceCalls = 0;
        let gateRaceRuntime;
        gateRaceRuntime = runtime(async () => {
          gateRaceCalls += 1;
          return response(401, { error: 'authentication_required' });
        }, async () => {
          // A session boundary queued while the shared password gate settles
          // must beat every older mutation waiter.
          gateRaceRuntime.switchAccount('different-account-token');
        });
        await assert.rejects(
          gateRaceRuntime.api('/games/12/join', { method: 'POST', body: '{"slot":1}' }),
          (error) => error.code === 'stale_session' && error.isStaleSession === true,
        );
        assert.equal(gateRaceCalls, 1, 'an account switch at gate settlement must stop replay');
      })().catch((error) => { console.error(error); process.exitCode = 1; });
    """.replace('__SOURCE__', json.dumps(source))
    subprocess.run(
        ['node', '-e', harness], cwd=ROOT, check=True,
        capture_output=True, text=True,
    )
