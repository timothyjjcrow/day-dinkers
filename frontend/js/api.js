/**
 * API client — handles all HTTP requests to the backend.
 * Automatically clears stale sessions on 401 responses.
 */
const API = {
    baseUrl: '',

    _isMutatingMethod(method) {
        return ['POST', 'PUT', 'PATCH', 'DELETE'].includes(String(method || '').toUpperCase());
    },

    clearCsrfToken() {
        localStorage.removeItem('csrf_token');
        localStorage.removeItem('csrf_token_for');
    },

    async _ensureCsrfToken(token) {
        const currentToken = String(token || '').trim();
        if (!currentToken) return null;

        const cachedToken = localStorage.getItem('csrf_token');
        const cachedFor = localStorage.getItem('csrf_token_for');
        if (cachedToken && cachedFor === currentToken) {
            return cachedToken;
        }

        try {
            const response = await fetch(API.baseUrl + '/api/auth/csrf', {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${currentToken}`,
                },
            });
            if (!response.ok) {
                API.clearCsrfToken();
                return null;
            }
            const data = await response.json();
            const csrfToken = String(data.csrf_token || '').trim();
            if (!csrfToken) {
                API.clearCsrfToken();
                return null;
            }
            localStorage.setItem('csrf_token', csrfToken);
            localStorage.setItem('csrf_token_for', currentToken);
            return csrfToken;
        } catch {
            API.clearCsrfToken();
            return null;
        }
    },

    async primeCsrfToken() {
        const token = localStorage.getItem('token');
        if (!token) {
            API.clearCsrfToken();
            return null;
        }
        return API._ensureCsrfToken(token);
    },

    async _fetchJson(url, options) {
        const response = await fetch(url, options);
        const contentType = String(response.headers.get('content-type') || '').toLowerCase();
        if (contentType.includes('application/json')) {
            const data = await response.json();
            return { response, data };
        }
        const text = await response.text();
        return { response, data: text ? { error: text } : {} };
    },

    async _request(method, url, body = null) {
        const headers = { 'Content-Type': 'application/json' };
        const token = localStorage.getItem('token');
        if (token) headers['Authorization'] = `Bearer ${token}`;
        if (token && API._isMutatingMethod(method)) {
            const csrfToken = await API._ensureCsrfToken(token);
            if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
        }

        const options = { method, headers };
        if (body) options.body = JSON.stringify(body);

        let { response, data } = await API._fetchJson(API.baseUrl + url, options);

        if (
            response.status === 403
            && data?.error === 'Invalid CSRF token'
            && token
            && API._isMutatingMethod(method)
        ) {
            API.clearCsrfToken();
            const freshCsrfToken = await API._ensureCsrfToken(token);
            if (freshCsrfToken) {
                options.headers['X-CSRF-Token'] = freshCsrfToken;
                ({ response, data } = await API._fetchJson(API.baseUrl + url, options));
            }
        }

        // Auto-clear stale sessions: if server says UNAUTHORIZED,
        // the token is invalid (e.g. DB was rebuilt). Clear it.
        if (response.status === 401 && token) {
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            API.clearCsrfToken();
            if (typeof Auth !== 'undefined') Auth.updateUI(null);
            if (typeof LocationService !== 'undefined' && typeof LocationService.clearCheckedInCourt === 'function') {
                LocationService.clearCheckedInCourt();
            }
        }

        if (!response.ok) {
            const err = new Error(data.error || 'Request failed');
            if (data.errors) err.details = data.errors;
            err.status = response.status;
            err.payload = data;
            throw err;
        }
        return data;
    },

    get(url) { return API._request('GET', url); },
    post(url, body) { return API._request('POST', url, body); },
    put(url, body) { return API._request('PUT', url, body); },
    delete(url) { return API._request('DELETE', url); },
};
