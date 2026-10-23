'use strict';

const handoffSoundUrl = document.currentScript.dataset.soundUrl;
let activeConversationId = null;
let lastConversationsJSON = '';
let lastMessagesJSON = '';
let renderedThroughId = 0;
let listRequest = 0;
let messageRequest = 0;
const drafts = new Map();
const acknowledged = new Map();
const acknowledging = new Map();
const escapeHandoff = WebVitasNotifications.escape;
const platformConfig = {
    whatsapp: {icon: 'fab fa-whatsapp', label: 'واتساب', css: 'whatsapp'},
    messenger: {icon: 'fab fa-facebook-messenger', label: 'ماسنجر', css: 'messenger'},
    instagram: {icon: 'fab fa-instagram', label: 'انستجرام', css: 'instagram'},
    facebook: {icon: 'fab fa-facebook', label: 'تعليقات فيسبوك', css: 'messenger'},
    web: {icon: 'fas fa-globe', label: 'ويب', css: 'web'},
};

function handoffFetch(url, options = {}) {
    return window.authFetch(url, options);
}

function viewedConversationId() {
    if (!activeConversationId || !renderedThroughId || document.visibilityState !== 'visible') return null;
    if (!document.getElementById('chat-area').getClientRects().length) return null;
    if (window.parent !== window) {
        if (window.parent.document.visibilityState !== 'visible' || !window.parent.document.hasFocus()) return null;
        if (!window.frameElement?.getClientRects().length) return null;
    } else if (!document.hasFocus()) return null;
    return activeConversationId;
}

const handoffNotifier = window.parent !== window && window.parent.dashboardNotifications
    ? window.parent.dashboardNotifications
    : WebVitasNotifications.create({
        request: () => handoffFetch('/dashboard/api/notifications/'),
        soundUrl: handoffSoundUrl,
        viewedConversation: viewedConversationId,
    });
// A click inside an iframe does not bubble to the dashboard's document.
document.addEventListener('pointerdown', () => handoffNotifier.unlockAudio());
document.addEventListener('keydown', () => handoffNotifier.unlockAudio());

function getPlatformBadgeHTML(platform) {
    const cfg = platformConfig[platform] || platformConfig.web;
    return `<span class="platform-badge ${cfg.css}"><i class="${cfg.icon}"></i> ${cfg.label}</span>`;
}

function formatDate(iso) {
    const date = new Date(iso);
    const today = date.toDateString() === new Date().toDateString();
    return date.toLocaleString('ar-EG', {
        ...(today ? {} : {month: 'short', day: 'numeric'}), hour: '2-digit', minute: '2-digit',
    });
}

function buildSenderInfo(platform, senderId) {
    if (!senderId) return '';
    if (platform === 'whatsapp') {
        const digits = senderId.replace(/\D/g, '');
        return digits.startsWith('20') && digits.length === 12 ? '0' + digits.slice(2) : digits;
    }
    if (platform === 'instagram') return `@${senderId}`;
    if (platform === 'messenger') return `fb: ${senderId}`;
    return senderId;
}

async function fetchConversations() {
    const requestId = ++listRequest;
    try {
        const res = await handoffFetch('/api/handoff/conversations/');
        if (!res?.ok) throw new Error('Conversation list unavailable');
        const data = await res.json();
        if (requestId !== listRequest) return;
        const json = JSON.stringify(data.conversations);
        if (json === lastConversationsJSON) return;
        lastConversationsJSON = json;
        const list = document.getElementById('conv-list');
        const focusedId = document.activeElement?.dataset.conversationId;
        const scrollTop = list.scrollTop;
        list.replaceChildren();
        if (!data.conversations.length) {
            list.textContent = 'لا توجد محادثات معلقة حالياً';
        }
        for (const conv of data.conversations) {
            const row = document.createElement('div');
            const unread = conv.unread_count || 0;
            row.setAttribute('role', 'button');
            row.tabIndex = 0;
            row.dataset.conversationId = conv.id;
            row.className = `conv-item ${activeConversationId === conv.id ? 'active' : ''} ${unread ? 'unread' : ''}`;
            row.setAttribute('aria-label', `محادثة ${conv.id}${unread ? `، ${unread} رسائل غير مقروءة` : ''}`);
            row.onclick = () => openConversation(conv.id, conv.platform, conv.platform_sender_id);
            row.innerHTML = `
                <div class="conv-header">
                    <span>عميل #${conv.id} ${getPlatformBadgeHTML(conv.platform)}</span>
                    <time datetime="${escapeHandoff(conv.last_message_at || conv.created_at)}">${formatDate(conv.last_message_at || conv.created_at)}</time>
                </div>
                <div class="conv-sender">${escapeHandoff(buildSenderInfo(conv.platform, conv.platform_sender_id))}</div>
                <div class="conv-summary"><div class="conv-preview">${escapeHandoff(conv.last_message)}</div>
                ${unread ? `<span class="unread-badge">${unread > 99 ? '99+' : unread}</span>` : ''}</div>`;
            list.appendChild(row);
        }
        list.scrollTop = scrollTop;
        if (focusedId) list.querySelector(`[data-conversation-id="${focusedId}"]`)?.focus({preventScroll: true});
    } catch (error) {
        if (requestId === listRequest) window.WebVitasUI?.showError();
    }
}

async function acknowledgeVisibleMessages() {
    const id = viewedConversationId();
    const through = renderedThroughId;
    if (!id || through <= (acknowledged.get(id) || 0) || acknowledging.has(id)) return;
    acknowledging.set(id, through);
    try {
        const res = await handoffFetch(`/api/handoff/conversations/${id}/read/`, {
            method: 'POST', body: JSON.stringify({through_message_id: through}),
        });
        if (!res?.ok) throw new Error('Read acknowledgement failed');
        acknowledged.set(id, Math.max(through, acknowledged.get(id) || 0));
        await fetchConversations();
    } catch (_) { /* Keep unread state and retry on the next refresh. */ }
    finally { acknowledging.delete(id); }
}

async function openConversation(id, platform, senderId) {
    window.WebVitasUI?.openConversation();
    if (activeConversationId === id) {
        await fetchMessages();
        return;
    }
    const input = document.getElementById('chat-input');
    if (activeConversationId) drafts.set(activeConversationId, input.value);
    input.value = drafts.get(id) || '';
    activeConversationId = id;
    renderedThroughId = 0;
    lastMessagesJSON = '';
    document.querySelectorAll('.conv-item').forEach(row => row.classList.toggle('active', Number(row.dataset.conversationId) === id));
    document.getElementById('no-selection').style.display = 'none';
    document.getElementById('chat-area').style.display = 'flex';
    document.getElementById('current-conv-id').textContent = id;
    document.getElementById('current-platform-badge').innerHTML = getPlatformBadgeHTML(platform);
    document.getElementById('current-sender-info').textContent = buildSenderInfo(platform, senderId);
    document.getElementById('btn-resolve').style.display = 'block';
    document.getElementById('chat-messages').textContent = 'جاري تحميل المحادثة…';
    await fetchMessages();
}

function renderMessages(messages) {
    const container = document.getElementById('chat-messages');
    container.replaceChildren();
    if (!messages.length) container.textContent = 'لا توجد رسائل سابقة.';
    for (const msg of messages) {
        const bubble = document.createElement('div');
        bubble.className = `message ${['user', 'agent', 'assistant'].includes(msg.role) ? msg.role : 'user'}`;
        const content = document.createElement('div');
        content.className = 'message-content';
        content.textContent = (msg.content || '').replace(/\\n/g, '\n');
        bubble.appendChild(content);
        if (msg.attachment_url) {
            try {
                const url = new URL(msg.attachment_url);
                if (['https:', 'http:'].includes(url.protocol)) {
                    const link = document.createElement('a');
                    link.href = url.href;
                    link.target = '_blank';
                    link.rel = 'noopener';
                    const img = document.createElement('img');
                    img.src = url.href;
                    img.alt = 'مرفق من العميل';
                    img.className = 'message-attachment';
                    img.onerror = () => { link.textContent = 'فتح المرفق'; };
                    link.appendChild(img);
                    bubble.appendChild(link);
                }
            } catch (_) { /* Invalid attachment links are not rendered. */ }
        }
        const time = document.createElement('div');
        time.className = 'message-time';
        time.textContent = formatDate(msg.created_at);
        bubble.appendChild(time);
        container.appendChild(bubble);
    }
    container.scrollTop = container.scrollHeight;
}

async function fetchMessages() {
    const id = activeConversationId;
    if (!id) return;
    const requestId = ++messageRequest;
    try {
        const res = await handoffFetch(`/api/handoff/conversations/${id}/messages/`);
        if (!res?.ok) throw new Error('Messages unavailable');
        const data = await res.json();
        if (activeConversationId !== id || requestId !== messageRequest) return;
        const json = JSON.stringify(data.messages);
        if (json !== lastMessagesJSON) {
            renderMessages(data.messages);
            lastMessagesJSON = json;
            renderedThroughId = Math.max(0, ...data.messages.map(m => m.id || 0));
        }
        // Visibility can change even when the server's messages have not changed.
        await acknowledgeVisibleMessages();
    } catch (_) {
        if (activeConversationId === id && requestId === messageRequest) window.WebVitasUI?.showError();
    }
}

async function sendMessage() {
    const id = activeConversationId;
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    const button = document.querySelector('.btn-send');
    if (!id || !text || button.disabled) return;
    button.disabled = true;
    input.value = '';
    drafts.delete(id);
    try {
        const res = await handoffFetch(`/api/handoff/conversations/${id}/reply/`, {
            method: 'POST', body: JSON.stringify({message: text}),
        });
        if (!res?.ok) throw new Error('Reply delivery failed');
        if (activeConversationId === id) await fetchMessages();
        await fetchConversations();
    } catch (_) {
        drafts.set(id, text);
        if (activeConversationId === id && !input.value) input.value = text;
        window.WebVitasUI?.showError();
    } finally { button.disabled = false; }
}

function handleKeyPress(event) {
    if (event.key === 'Enter') sendMessage();
}

async function resolveConversation() {
    const id = activeConversationId;
    if (!id || !confirm('هل أنت متأكد من حل هذه المشكلة وإغلاق المحادثة؟')) return;
    try {
        const res = await handoffFetch(`/api/handoff/conversations/${id}/resolve/`, {method: 'POST'});
        if (!res?.ok) throw new Error('Resolution failed');
        if (activeConversationId === id) {
            activeConversationId = null;
            renderedThroughId = 0;
            messageRequest++;
            document.getElementById('no-selection').style.display = 'flex';
            document.getElementById('chat-area').style.display = 'none';
            document.body.classList.remove('conversation-open');
        }
        drafts.delete(id);
        await fetchConversations();
    } catch (_) { window.WebVitasUI?.showError(); }
}

const handoffPoller = WebVitasPolling.watch(
    () => Promise.allSettled([fetchConversations(), fetchMessages()]), 10000);
function refreshHandoff() { return handoffPoller.refresh(); }
window.handoffAttention = {viewedConversationId, refresh: refreshHandoff};
if (window.parent === window || !window.parent.dashboardNotifications) {
    WebVitasPolling.watch(() => handoffNotifier.poll(), 10000, {background: true});
}
