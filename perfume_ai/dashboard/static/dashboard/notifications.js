/* Shared by the dashboard shell and the standalone handoff page. */
(() => {
    'use strict';
    window.WebVitasNotifications = {
        create({request, soundUrl, onData = () => {}, viewedConversation = () => null, quiet = () => false}) {
            let initialized = false;
            let pending = null;
            const seen = new Set();
            const audio = new Audio(soundUrl);
            let unlocked = false;
            let unlocking = false;

            async function unlockAudio() {
                if (unlocked || unlocking) return;
                unlocking = true;
                audio.muted = true;
                try {
                    await audio.play();
                    audio.pause();
                    audio.currentTime = 0;
                    unlocked = true;
                } catch (_) { /* Another user gesture can retry. */ }
                finally { audio.muted = false; unlocking = false; }
            }
            document.addEventListener('pointerdown', unlockAudio);
            document.addEventListener('keydown', unlockAudio);

            function observe(data, silent = false) {
                const rows = data.notifications || [];
                const viewed = viewedConversation();
                const shouldRing = initialized && !silent && !quiet() && rows.some(n =>
                    !seen.has(n.id) && !n.is_read &&
                    !(n.conversation_id != null && n.conversation_id === viewed));
                rows.forEach(n => seen.add(n.id));
                initialized = true;
                onData(data);
                if (shouldRing && !unlocking) {
                    audio.currentTime = 0;
                    audio.play().catch(() => {}); // Visual alerts do not depend on autoplay permission.
                }
            }

            function poll() {
                if (pending) return pending;
                pending = (async () => {
                    try {
                        const response = await request();
                        if (response?.ok) observe(await response.json());
                    } catch (_) { /* Preserve the last successful snapshot on network failures. */ }
                    finally { pending = null; }
                })();
                return pending;
            }
            return {poll, observe, unlockAudio};
        },
        escape(value) {
            return String(value ?? '').replace(/[&<>"']/g, c => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
            }[c]));
        },
    };
})();
