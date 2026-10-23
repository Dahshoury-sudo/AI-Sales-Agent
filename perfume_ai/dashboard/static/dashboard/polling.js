/* Request budgets are shared by the dashboard and its same-origin frames. */
(() => {
    'use strict';
    let shared;
    try { if (parent !== window) shared = parent.WebVitasPolling; } catch (_) {}
    const buckets = new Map();
    const pending = new Map();

    function retryDelay(response, failures) {
        const value = response?.headers.get('Retry-After');
        if (value) {
            const milliseconds = /^\d+(\.\d+)?$/.test(value.trim())
                ? Number(value) * 1000 : Date.parse(value) - Date.now();
            if (Number.isFinite(milliseconds) && milliseconds >= 0) return Math.max(1000, milliseconds);
        }
        return Math.min(60000, 5000 * 2 ** Math.min(failures - 1, 4));
    }

    async function request(scope, url, options = {}, transport = fetch) {
        const state = buckets.get(scope) || {until: 0, failures: 0, generation: 0};
        buckets.set(scope, state);
        const key = scope + ':' + url;
        if (state.until > Date.now()) {
            return new Response(JSON.stringify({detail: 'Waiting before retrying.'}), {
                status: 429, headers: {'Content-Type': 'application/json',
                    'Retry-After': String(Math.ceil((state.until - Date.now()) / 1000))},
            });
        }
        // Callers with individual cancellation signals retain independent lifetimes.
        let work = !options.signal && pending.get(key);
        if (!work) {
            const generation = state.generation;
            work = (async () => {
                try {
                    const response = await transport(url, options);
                    if (response?.status === 429 || response?.status >= 500) {
                        state.failures++;
                        state.generation++;
                        state.until = Math.max(state.until, Date.now() + retryDelay(response, state.failures));
                    } else if (response?.ok && state.generation === generation) {
                        state.failures = 0;
                        state.until = 0;
                    }
                    return response;
                } catch (error) {
                    if (error.name !== 'AbortError') {
                        state.failures++;
                        state.generation++;
                        state.until = Math.max(state.until, Date.now() + retryDelay(null, state.failures));
                    }
                    throw error;
                }
            })();
            if (!options.signal) pending.set(key, work);
        }
        try { return (await work)?.clone() || null; }
        finally { if (pending.get(key) === work) pending.delete(key); }
    }

    function visible() {
        if (document.hidden || document.visibilityState === 'hidden') return false;
        try {
            if (parent !== window && (parent.document.hidden || !window.frameElement?.getClientRects().length)) return false;
        } catch (_) {}
        return true;
    }

    function watch(callback, interval, {background = false} = {}) {
        let running = null;
        let stopped = false;
        function refresh() {
            if (stopped || (!background && !visible()) || window.WebVitasAuth?.ended()) return;
            if (running) return running;
            running = Promise.resolve().then(callback)
                .catch(() => { /* Request handling retains data until the next retry. */ })
                .finally(() => { running = null; });
            return running;
        }
        const timer = setInterval(refresh, interval);
        document.addEventListener('visibilitychange', refresh);
        window.addEventListener('focus', refresh);
        window.addEventListener('webvitas:section', refresh);
        refresh();
        return {refresh, stop() {
            stopped = true;
            clearInterval(timer);
            document.removeEventListener('visibilitychange', refresh);
            window.removeEventListener('focus', refresh);
            window.removeEventListener('webvitas:section', refresh);
        }};
    }

    // Scheduling uses the local frame's visibility; network budgets live in the parent.
    window.WebVitasPolling = {request: shared?.request || request, retryDelay, visible, watch};
})();
