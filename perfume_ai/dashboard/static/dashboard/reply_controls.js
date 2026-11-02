(() => {
    'use strict';
    const panel = document.getElementById('reply-controls');
    const controls = [...panel.querySelectorAll('[data-reply-field]')];
    const status = document.getElementById('reply-controls-status');
    const retry = document.getElementById('reply-controls-retry');
    const endpoint = '/dashboard/api/settings/';
    const confirmed = {};
    let busy = false;
    let loaded = false;

    function setBusy(value) {
        busy = value;
        panel.setAttribute('aria-busy', String(value));
        controls.forEach(control => { control.disabled = value || !loaded; });
        retry.disabled = value;
    }

    function feedback(message, error = false) {
        status.textContent = message;
        status.classList.toggle('is-error', error);
        retry.hidden = !error;
    }

    async function load() {
        if (busy) return;
        setBusy(true);
        feedback('جاري تحميل إعدادات الرد…');
        try {
            const response = await window.WebVitasAuth.fetch(endpoint);
            if (!response?.ok) throw new Error('Settings load failed');
            const settings = await response.json();
            if (controls.some(control => typeof settings[control.dataset.replyField] !== 'boolean')) {
                throw new Error('Missing reply settings');
            }
            controls.forEach(control => {
                confirmed[control.dataset.replyField] = settings[control.dataset.replyField];
                control.checked = confirmed[control.dataset.replyField];
            });
            loaded = true;
            feedback('التغييرات تُحفظ فوراً.');
        } catch (_) {
            loaded = false;
            feedback('تعذّر تحميل إعدادات الرد. أعد المحاولة.', true);
        } finally {
            setBusy(false);
        }
    }

    async function save(control) {
        const field = control.dataset.replyField;
        if (busy || !loaded) {
            control.checked = confirmed[field] === true;
            return;
        }
        const value = control.checked;
        setBusy(true);
        feedback('جاري حفظ التغيير…');
        try {
            const response = await window.WebVitasAuth.fetch(endpoint, {
                method: 'PUT', body: JSON.stringify({ [field]: value }),
            });
            if (!response?.ok) throw new Error('Settings save failed');
            confirmed[field] = value;
            feedback('تم حفظ إعدادات الرد.');
        } catch (_) {
            control.checked = confirmed[field];
            feedback('تعذّر حفظ التغيير. تم استرجاع آخر إعداد مؤكد؛ يمكنك إعادة تحميل الإعدادات.', true);
        } finally {
            setBusy(false);
            // Native disabled inputs lose keyboard focus while the save is pending.
            if (document.activeElement === document.body && control.getClientRects().length) control.focus();
        }
    }

    controls.forEach(control => control.addEventListener('change', () => save(control)));
    retry.addEventListener('click', load);
    window.ReplyControls = { load };
})();
