/**
 * Authentication module — login, register, session management.
 */
const Auth = {
    googleInitAttempts: 0,

    showModal() {
        document.getElementById('auth-modal').style.display = 'flex';
        document.getElementById('login-error').textContent = '';
        document.getElementById('register-error').textContent = '';
        Auth._setGoogleError('');
        Auth.googleInitAttempts = 0;
        Auth.prepareGoogleSignIn();
    },

    hideModal() {
        document.getElementById('auth-modal').style.display = 'none';
    },

    switchTab(tab) {
        document.getElementById('login-form').style.display = tab === 'login' ? 'block' : 'none';
        document.getElementById('register-form').style.display = tab === 'register' ? 'block' : 'none';
        document.querySelectorAll('.auth-tab').forEach((btn, i) => {
            btn.classList.toggle('active', (i === 0 && tab === 'login') || (i === 1 && tab === 'register'));
        });
        if (tab === 'login') {
            Auth.prepareGoogleSignIn();
        } else {
            const wrapper = document.getElementById('google-auth-wrapper');
            if (wrapper) wrapper.style.display = 'none';
        }
    },

    _finishAuth(res, welcomeMessage) {
        localStorage.setItem('token', res.token);
        localStorage.setItem('user', JSON.stringify(res.user));
        Auth.hideModal();
        Auth.updateUI(res.user);
        if (typeof LocationService !== 'undefined' && typeof LocationService.syncWithServerStatus === 'function') {
            LocationService.syncWithServerStatus();
        }
        App.loadFriendsCache();
        App.refreshReviewerAccess();
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
            Auth._finishAuth(res, `Welcome to PicklePlay, ${res.user.name || res.user.username}!`);
        } catch (err) {
            document.getElementById('register-error').textContent = err.message || 'Registration failed';
        }
    },

    logout() {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        if (typeof LocationService !== 'undefined' && typeof LocationService.clearCheckedInCourt === 'function') {
            LocationService.clearCheckedInCourt();
        }
        Auth.updateUI(null);
        App.refreshReviewerAccess();
        App.showView('map');
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
            Auth.updateUI(res.user);
            if (typeof LocationService !== 'undefined' && typeof LocationService.syncWithServerStatus === 'function') {
                LocationService.syncWithServerStatus();
            }
            App.loadFriendsCache();
            App.refreshReviewerAccess();
        } catch {
            // Token is stale/invalid — clear session silently
            localStorage.removeItem('token');
            localStorage.removeItem('user');
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
            authBtn.style.display = 'none';
            profileBtn.style.display = 'inline-flex';
        } else {
            authBtn.style.display = 'inline-flex';
            profileBtn.style.display = 'none';
        }
    },
};
