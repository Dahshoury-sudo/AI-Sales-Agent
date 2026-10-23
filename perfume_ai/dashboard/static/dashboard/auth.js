/* One authentication path for standalone pages, dashboard frames and writes. */
(() => {
    'use strict';
    try {
        if (parent !== window && parent.WebVitasAuth) {
            window.WebVitasAuth = parent.WebVitasAuth;
            window.authFetch = parent.WebVitasAuth.fetch;
            return;
        }
    } catch (_) {}

    let refreshing = null;
    let signedOut = false;
    const retryKey = 'webvitas_refresh_retry';
    const token = () => localStorage.getItem('access_token');

    function nearExpiry(value) {
        if (!value) return true;
        try {
            const encoded = value.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
            const payload = JSON.parse(atob(encoded));
            return Number.isFinite(payload.exp) && payload.exp * 1000 <= Date.now() + 60000;
        } catch (_) { return false; } // The server remains the authority on token validity.
    }

    function logout() {
        if (signedOut) return;
        signedOut = true;
        for (const key of ['access_token', 'refresh_token', 'store_api_key', 'perfume_api_key', retryKey]) {
            localStorage.removeItem(key);
        }
        window.location.replace('/dashboard/login/');
    }

    function retryState() {
        try { return JSON.parse(localStorage.getItem(retryKey)) || {}; }
        catch (_) { return {}; }
    }

    async function refreshAccess(rejected, force = false) {
        if (refreshing) return refreshing;
        async function refresh() {
            if (signedOut) return null;
            const current = token();
            if ((!force || current !== rejected) && !nearExpiry(current)) return current;
            const refreshToken = localStorage.getItem('refresh_token');
            if (!refreshToken) { logout(); return null; }
            const state = retryState();
            if (state.until > Date.now()) {
                throw new Error('Token refresh is cooling down.');
            }
            try {
                const response = await fetch('/dashboard/api/token/refresh/', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({refresh: refreshToken}),
                });
                // A logout or another tab's login must not be overwritten by a late refresh.
                if (signedOut || localStorage.getItem('refresh_token') !== refreshToken) return token();
                if ([400, 401, 403].includes(response.status)) { logout(); return null; }
                if (!response.ok) {
                    const error = new Error('Token refresh temporarily unavailable.');
                    error.response = response;
                    throw error;
                }
                const data = await response.json();
                if (!data.access) throw new Error('Token refresh returned no access token.');
                if (signedOut || localStorage.getItem('refresh_token') !== refreshToken) return token();
                localStorage.setItem('access_token', data.access);
                if (data.refresh) localStorage.setItem('refresh_token', data.refresh);
                localStorage.removeItem(retryKey);
                return data.access;
            } catch (error) {
                const failures = (state.failures || 0) + 1;
                localStorage.setItem(retryKey, JSON.stringify({failures,
                    until: Date.now() + WebVitasPolling.retryDelay(error.response, failures)}));
                throw error;
            }
        }
        refreshing = navigator.locks?.request
            ? navigator.locks.request('webvitas-token-refresh', refresh) : refresh();
        try { return await refreshing; }
        finally { refreshing = null; }
    }

    async function authenticated(url, options) {
        if (signedOut) return null;
        options.signal?.throwIfAborted();
        let access = token();
        if (nearExpiry(access)) access = await refreshAccess(access);
        if (!access || signedOut) return null;
        const headers = new Headers(options.headers);
        headers.set('Authorization', `Bearer ${access}`);
        // FormData can originate in an iframe, so instanceof is insufficient.
        if (options.body && Object.prototype.toString.call(options.body) !== '[object FormData]' && !headers.has('Content-Type')) {
            headers.set('Content-Type', 'application/json');
        }
        options.signal?.throwIfAborted();
        let response = await fetch(url, {...options, headers});
        if (response.status === 401) {
            access = await refreshAccess(access, true);
            if (!access || signedOut) return null;
            options.signal?.throwIfAborted();
            headers.set('Authorization', `Bearer ${access}`);
            response = await fetch(url, {...options, headers});
            if (response.status === 401) { logout(); return null; }
        }
        return response;
    }

    async function authFetch(url, options = {}) {
        const {suppressError, ...init} = options;
        if (new URL(url, location.href).origin !== location.origin) throw new Error('Authenticated requests must be same-origin.');
        const path = new URL(url, location.href).pathname;
        const polling = (init.method || 'GET').toUpperCase() === 'GET' && (
            /^\/api\/(history\/conversations\/|handoff\/conversations\/(\d+\/messages\/)?|orders\/dashboard\/)$/.test(path) ||
            path === '/dashboard/api/notifications/');
        try {
            if (signedOut) return null;
            const response = polling
                ? await WebVitasPolling.request('dashboard_poll', url, init, authenticated)
                : await authenticated(url, init);
            if (response && !response.ok && !suppressError) window.WebVitasUI?.showError();
            return response;
        } catch (error) {
            if (error.name !== 'AbortError' && !suppressError) window.WebVitasUI?.showError();
            throw error;
        }
    }

    window.addEventListener('storage', event => {
        if ((event.key === 'access_token' || event.key === null) && !token()) logout();
    });
    window.WebVitasAuth = {fetch: authFetch, logout, ended: () => signedOut};
    window.authFetch = authFetch;
})();
