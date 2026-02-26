/**
 * Authentication module — login, register, session management.
 */
const Auth = {
    googleInitAttempts: 0,

    _showAuthForm(formId) {
        const allFormIds = ['login-form', 'register-form', 'reset-request-form', 'reset-confirm-form'];
        allFormIds.forEach((id) => {
            const form = document.getElementById(id);
            if (form) form.style.display = id === formId ? 'block' : 'none';
        });
    },

    _clearResetMessages() {
        const ids = [
            'reset-request-error',
            'reset-request-status',
            'reset-confirm-error',
            'reset-confirm-status',
        ];
        ids.forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.textContent = '';
        });
    },

    showModal() {
        document.getElementById('auth-modal').style.display = 'flex';
        Auth.switchTab('login');
    },

    hideModal() {
        document.getElementById('auth-modal').style.display = 'none';
    },

    switchTab(tab) {
        Auth._showAuthForm(tab === 'login' ? 'login-form' : 'register-form');
        document.getElementById('login-error').textContent = '';
        document.getElementById('register-error').textContent = '';
        Auth._clearResetMessages();
        Auth._setGoogleError('');
        document.querySelectorAll('.auth-tab').forEach((btn, i) => {
            btn.classList.toggle('active', (i === 0 && tab === 'login') || (i === 1 && tab === 'register'));
        });
        Auth.googleInitAttempts = 0;
        if (tab === 'login') {
            Auth.prepareGoogleSignIn();
        } else {
            const wrapper = document.getElementById('google-auth-wrapper');
            if (wrapper) wrapper.style.display = 'none';
        }
    },

    showResetRequestForm() {
        Auth._showAuthForm('reset-request-form');
        Auth._clearResetMessages();
        Auth._setGoogleError('');
        document.querySelectorAll('.auth-tab').forEach((btn) => btn.classList.remove('active'));
        const wrapper = document.getElementById('google-auth-wrapper');
        if (wrapper) wrapper.style.display = 'none';
    },

    showResetConfirmForm(prefillToken = '') {
        Auth._showAuthForm('reset-confirm-form');
        Auth._clearResetMessages();
        Auth._setGoogleError('');
        document.querySelectorAll('.auth-tab').forEach((btn) => btn.classList.remove('active'));
        const wrapper = document.getElementById('google-auth-wrapper');
        if (wrapper) wrapper.style.display = 'none';
        const tokenInput = document.querySelector('#reset-confirm-form input[name="token"]');
        if (tokenInput && prefillToken && !tokenInput.value) {
            tokenInput.value = prefillToken;
        }
    },

    _finishAuth(res, welcomeMessage) {
        if (typeof API !== 'undefined' && typeof API.clearCsrfToken === 'function') {
            API.clearCsrfToken();
        }
        localStorage.setItem('token', res.token);
        localStorage.setItem('user', JSON.stringify(res.user));
        if (typeof API !== 'undefined' && typeof API.primeCsrfToken === 'function') {
            API.primeCsrfToken();
        }
        Auth.hideModal();
        Auth.updateUI(res.user);
        if (typeof LocationService !== 'undefined' && typeof LocationService.syncWithServerStatus === 'function') {
            LocationService.syncWithServerStatus();
        }
        App.loadFriendsCache();
        App.refreshReviewerAccess();
        if (typeof App.refreshNotificationBadge === 'function') {
            App.refreshNotificationBadge();
        }
        App.toast(welcomeMessage);
    },

    _setGoogleError(message) {
        const errorEl = document.getElementById('google-auth-error');
        if (errorEl) errorEl.textContent = message || '';
    },

    async prepareGoogleSignIn() {
        const wrapper = document.getElementById('google-auth-wrapper');
        const buttonEl = document.getElementById('google-signin-btn');
        const loginForm = document.getElementById('login-form');
        if (!wrapper || !buttonEl || !loginForm) return;
        if (loginForm.style.display === 'none') return;

        buttonEl.innerHTML = '';
        Auth._setGoogleError('');
        wrapper.style.display = 'none';

        let config;
        try {
            config = await API.get('/api/auth/google/config');
        } catch {
            return;
        }

        const enabled = !!config?.enabled;
        const clientId = String(config?.client_id || '').trim();
        if (!enabled || !clientId) return;

        if (!window.google || !window.google.accounts || !window.google.accounts.id) {
            if (Auth.googleInitAttempts < 4) {
                Auth.googleInitAttempts += 1;
                setTimeout(() => Auth.prepareGoogleSignIn(), 250);
                return;
            }
            wrapper.style.display = 'block';
            Auth._setGoogleError('Google Sign-In is still loading. Please try again.');
            return;
        }

        try {
            window.google.accounts.id.initialize({
                client_id: clientId,
                callback: (response) => Auth.handleGoogleCredentialResponse(response),
            });
            window.google.accounts.id.renderButton(buttonEl, {
                theme: 'outline',
                size: 'large',
                text: 'continue_with',
                shape: 'pill',
                width: 300,
            });
            wrapper.style.display = 'block';
        } catch {
            wrapper.style.display = 'block';
            Auth._setGoogleError('Unable to initialize Google Sign-In.');
        }
    },

    async handleGoogleCredentialResponse(response) {
        if (!response || !response.credential) {
            Auth._setGoogleError('Google did not return a valid sign-in token.');
            return;
        }
        try {
            const res = await API.post('/api/auth/google', { id_token: response.credential });
            Auth._finishAuth(res, `Welcome, ${res.user.name || res.user.username}!`);
        } catch (err) {
            Auth._setGoogleError(err.message || 'Google sign-in failed');
        }
    },

    async login(e) {
        e.preventDefault();
        const form = e.target;
        const data = { email: form.email.value, password: form.password.value };
        document.getElementById('login-error').textContent = '';
        try {
            const res = await API.post('/api/auth/login', data);
            Auth._finishAuth(res, `Welcome back, ${res.user.name || res.user.username}!`);
        } catch (err) {
            document.getElementById('login-error').textContent = err.message || 'Login failed';
        }
    },

    async register(e) {
        e.preventDefault();
        const form = e.target;
        const data = {
            username: form.username.value,
            email: form.email.value,
            password: form.password.value,
            name: form.name.value,
            skill_level: form.skill_level.value ? parseFloat(form.skill_level.value) : null,
            play_style: form.play_style.value,
        };
        document.getElementById('register-error').textContent = '';
        try {
            const res = await API.post('/api/auth/register', data);
            Auth._finishAuth(res, `Welcome to Third Shot, ${res.user.name || res.user.username}!`);
        } catch (err) {
            document.getElementById('register-error').textContent = err.message || 'Registration failed';
        }
    },

    async requestPasswordReset(e) {
        e.preventDefault();
        const form = e.target;
        const email = String(form.email.value || '').trim();
        const errorEl = document.getElementById('reset-request-error');
        const statusEl = document.getElementById('reset-request-status');
        if (errorEl) errorEl.textContent = '';
        if (statusEl) statusEl.textContent = '';

        try {
            const res = await API.post('/api/auth/password-reset/request', { email });
            if (statusEl) {
                statusEl.textContent = res.message || 'If an account exists, reset instructions have been sent.';
            }
            if (res.reset_token) {
                Auth.showResetConfirmForm(res.reset_token);
                const confirmStatus = document.getElementById('reset-confirm-status');
                if (confirmStatus) {
                    confirmStatus.textContent = 'Reset token auto-filled for this environment.';
                }
            }
        } catch (err) {
            if (errorEl) errorEl.textContent = err.message || 'Unable to request password reset';
        }
    },

    async confirmPasswordReset(e) {
        e.preventDefault();
        const form = e.target;
        const payload = {
            token: String(form.token.value || '').trim(),
            new_password: form.new_password.value,
        };
        const errorEl = document.getElementById('reset-confirm-error');
        const statusEl = document.getElementById('reset-confirm-status');
        if (errorEl) errorEl.textContent = '';
        if (statusEl) statusEl.textContent = '';

        try {
            await API.post('/api/auth/password-reset/confirm', payload);
            if (statusEl) statusEl.textContent = 'Password reset successful. You can now sign in.';
            const loginEmail = document.querySelector('#login-form input[name="email"]');
            if (loginEmail) loginEmail.value = String(form.email.value || '').trim();
            App.toast('Password reset successful');
            Auth.switchTab('login');
        } catch (err) {
            if (errorEl) errorEl.textContent = err.message || 'Unable to reset password';
        }
    },

    logout() {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        if (typeof API !== 'undefined' && typeof API.clearCsrfToken === 'function') {
            API.clearCsrfToken();
        }
        if (typeof LocationService !== 'undefined' && typeof LocationService.clearCheckedInCourt === 'function') {
            LocationService.clearCheckedInCourt();
        }
        Auth.updateUI(null);
        App.refreshReviewerAccess();
        if (typeof App._setNotificationBadge === 'function') {
            App._setNotificationBadge(0);
        }
        App.setMainTab('map');
        App.toast('Signed out');
    },

    async checkAuth() {
        const token = localStorage.getItem('token');
        const user = JSON.parse(localStorage.getItem('user') || 'null');

        if (!token || !user) {
            // No session at all — show sign-in button, stay on map
            if (typeof LocationService !== 'undefined' && typeof LocationService.clearCheckedInCourt === 'function') {
                LocationService.clearCheckedInCourt();
            }
            Auth.updateUI(null);
            App.refreshReviewerAccess();
            return;
        }

        // Validate the token is still valid against the server
        try {
            const res = await API.get('/api/auth/profile');
            // Token is valid — update stored user data and show profile button
            localStorage.setItem('user', JSON.stringify(res.user));
            if (typeof API !== 'undefined' && typeof API.primeCsrfToken === 'function') {
                API.primeCsrfToken();
            }
            Auth.updateUI(res.user);
            if (typeof LocationService !== 'undefined' && typeof LocationService.syncWithServerStatus === 'function') {
                LocationService.syncWithServerStatus();
            }
            App.loadFriendsCache();
            App.refreshReviewerAccess();
            if (typeof App.refreshNotificationBadge === 'function') {
                App.refreshNotificationBadge();
            }
        } catch {
            // Token is stale/invalid — clear session silently
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            if (typeof API !== 'undefined' && typeof API.clearCsrfToken === 'function') {
                API.clearCsrfToken();
            }
            if (typeof LocationService !== 'undefined' && typeof LocationService.clearCheckedInCourt === 'function') {
                LocationService.clearCheckedInCourt();
            }
            Auth.updateUI(null);
            App.refreshReviewerAccess();
        }
    },

    updateUI(user) {
        const authBtn = document.getElementById('btn-auth');
        const profileBtn = document.getElementById('btn-profile');
        if (user) {
            if (authBtn) authBtn.style.display = 'none';
            if (profileBtn) profileBtn.style.display = 'inline-flex';
            // Update avatar initial
            const initialEl = document.getElementById('header-user-initial');
            if (initialEl) {
                const name = user.name || user.username || '?';
                initialEl.textContent = name[0].toUpperCase();
            }
        } else {
            if (authBtn) authBtn.style.display = 'inline-flex';
            if (profileBtn) profileBtn.style.display = 'none';
        }
    },
};
