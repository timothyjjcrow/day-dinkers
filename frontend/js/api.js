/**
 * API client — handles all HTTP requests to the backend.
 * Automatically clears stale sessions on 401 responses.
 */
const API = {
    baseUrl: '',

    async _request(method, url, body = null) {
        const headers = { 'Content-Type': 'application/json' };
        const token = localStorage.getItem('token');
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const options = { method, headers };
        if (body) options.body = JSON.stringify(body);

        const response = await fetch(API.baseUrl + url, options);
        const data = await response.json();

        // Auto-clear stale sessions: if server says UNAUTHORIZED,
        // the token is invalid (e.g. DB was rebuilt). Clear it.
        if (response.status === 401 && token) {
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            if (typeof Auth !== 'undefined') Auth.updateUI(null);
            if (typeof LocationService !== 'undefined' && typeof LocationService.clearCheckedInCourt === 'function') {
                LocationService.clearCheckedInCourt();
            }
        }

        if (!response.ok) {
            const err = new Error(data.error || 'Request failed');
            if (data.errors) err.details = data.errors;
            err.status = response.status;
            throw err;
        }
        return data;
    },

    get(url) { return API._request('GET', url); },
    post(url, body) { return API._request('POST', url, body); },
    put(url, body) { return API._request('PUT', url, body); },
    delete(url) { return API._request('DELETE', url); },
};
