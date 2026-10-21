/* Real dashboard scripts, synthetic APIs; no business services or credentials. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {intercept, base, ready} = require('./check_layout.cjs');
const assert = require('node:assert/strict');

(async () => {
    const browser = await chromium.launch({channel: 'chrome', headless: true});
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
        await context.addInitScript(() => {
            localStorage.setItem('access_token', 'preview');
            localStorage.setItem('refresh_token', 'preview-refresh');
            window.bellCount = 0;
            window.pollIntervals = [];
            window.setInterval = (fn, ms) => { window.pollIntervals.push(ms); return 1; };
            window.Audio = class {
                muted = false;
                play() { if (!this.muted) window.bellCount++; return Promise.resolve(); }
                pause() {}
            };
        });
        await context.route('**/*', intercept);
        let sequence = 10;
        const chats = [1, 2, 3].map(id => ({id, platform: 'messenger', platform_sender_id: `sender${id}`,
            created_at: `2026-10-0${id}T10:00:00Z`, messages: [{id, role: 'user', content: `history ${id}`,
            created_at: `2026-10-0${id}T10:00:00Z`, unread: false}]}));
        const notifications = [{id: 1, type: 'new_order', title: 'Historical alert', message: 'old', is_read: false,
            created_at: '2026-10-01T10:00:00Z'}];
        const reads = [];
        const errors = [];
        let releaseDelayed;
        let delayChat = null;
        let notificationGets = 0;
        function addMessage(id, content = 'new customer message') {
            const message = {id: ++sequence, role: 'user', content, unread: true,
                created_at: new Date(Date.UTC(2026, 9, 22, 12, 0, sequence)).toISOString()};
            chats.find(c => c.id === id).messages.push(message);
            notifications.unshift({id: sequence, type: 'handoff_message', title: 'New message', message: content,
                conversation_id: id, source_message_id: sequence, is_read: false, created_at: message.created_at});
            return message;
        }
        function summary() {
            return chats.map(c => ({...c, messages: undefined, last_message: c.messages.at(-1).content,
                last_message_at: c.messages.at(-1).created_at, unread_count: c.messages.filter(m => m.unread).length}))
                .sort((a, b) => b.last_message_at.localeCompare(a.last_message_at));
        }
        await context.route('**/api/handoff/conversations/**', async route => {
            const url = new URL(route.request().url());
            const match = url.pathname.match(/conversations\/(\d+)\/(\w+)\//);
            let data;
            if (!match) data = {conversations: summary()};
            else {
                const id = Number(match[1]), action = match[2], chat = chats.find(c => c.id === id);
                if (action === 'messages') {
                    data = {conversation_id: id, needs_human: true, messages: structuredClone(chat.messages)};
                    if (delayChat === id) {
                        delayChat = null;
                        await new Promise(resolve => { releaseDelayed = resolve; });
                    }
                } else if (action === 'read') {
                    const through = route.request().postDataJSON().through_message_id;
                    reads.push({id, through});
                    chat.messages.filter(m => m.id <= through).forEach(m => { m.unread = false; });
                    notifications.filter(n => n.conversation_id === id && n.source_message_id <= through)
                        .forEach(n => { n.is_read = true; });
                    data = {unread_count: chat.messages.filter(m => m.unread).length};
                } else data = {status: 'ok'};
            }
            await route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
        });
        await context.route('**/dashboard/api/notifications/', route => {
            notificationGets++;
            return route.fulfill({contentType: 'application/json', body: JSON.stringify({notifications,
                unread_count: notifications.filter(n => !n.is_read).length})});
        });
        const page = await context.newPage();
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base + '/dashboard/', {waitUntil: 'networkidle'});
        await ready(page);
        await page.evaluate(() => _pollNotifications());
        assert.equal(await page.evaluate(() => bellCount), 0, 'Historical alerts stay silent');
        await page.evaluate(() => showSection('handoff'));
        const frame = await (await page.locator('#iframe-handoff').elementHandle()).contentFrame();
        await frame.locator('.conv-item').first().waitFor();
        assert.equal(await frame.locator('.conv-item').first().getAttribute('data-conversation-id'), '3');
        const getsBefore = notificationGets;
        await frame.evaluate(() => handoffAttention.refresh());
        assert.equal(notificationGets, getsBefore, 'Embedded frame does not poll notifications');
        await frame.locator('[data-conversation-id="2"]').click();
        await frame.waitForFunction(() => renderedThroughId === 2);
        await frame.locator('#chat-input').fill('draft stays here');
        addMessage(1, '<img src=x onerror="window.injected=true"> customer text');
        addMessage(1, 'second in batch');
        await frame.evaluate(() => handoffAttention.refresh());
        await page.evaluate(() => _pollNotifications());
        assert.equal(await frame.locator('.conv-item').first().getAttribute('data-conversation-id'), '1');
        assert.equal(await frame.locator('[data-conversation-id="1"] .unread-badge').innerText(), '2');
        assert.equal(await frame.locator('#chat-input').inputValue(), 'draft stays here');
        assert.equal(await frame.locator('#current-conv-id').innerText(), '2');
        assert.equal(await page.evaluate(() => bellCount), 1, 'One sound for a batch');
        assert.equal(await frame.evaluate(() => window.injected), undefined);
        await page.evaluate(() => _pollNotifications());
        assert.equal(await page.evaluate(() => bellCount), 1, 'Repeated snapshot is silent');
        // Replace one unread notification: equal count must still ring for a new ID.
        notifications.find(n => !n.is_read).is_read = true;
        addMessage(1);
        await page.evaluate(() => _pollNotifications());
        assert.equal(await page.evaluate(() => bellCount), 2);
        await frame.locator('[data-conversation-id="1"]').click();
        await frame.waitForFunction(() => acknowledged.get(1) >= 13);
        assert.equal(chats[0].messages.filter(m => m.unread).length, 0);
        const visible = addMessage(1);
        await page.evaluate(() => _pollNotifications());
        assert.equal(await page.evaluate(() => bellCount), 2, 'Visible active chat is silent');
        await frame.evaluate(() => handoffAttention.refresh());
        assert.equal(visible.unread, false);
        assert.match(await frame.locator('#chat-messages').innerText(), /<img src=x/);
        assert.equal(await frame.locator('#chat-messages img').count(), 0, 'Customer text is not HTML');

        await page.evaluate(() => showSection('products'));
        const hidden = addMessage(1);
        const readsBefore = reads.length;
        await frame.evaluate(() => handoffAttention.refresh());
        assert.equal(reads.length, readsBefore, 'Hidden dashboard section stays unread');
        await page.evaluate(() => _pollNotifications());
        assert.equal(await page.evaluate(() => bellCount), 3);
        await page.evaluate(() => showSection('handoff'));
        await frame.waitForFunction(id => acknowledged.get(1) >= id, hidden.id);
        assert.equal(hidden.unread, false, 'Returning acknowledges unchanged rendered content');

        await page.setViewportSize({width: 375, height: 812});
        await frame.locator('.conversation-back').click();
        const mobileMessage = addMessage(1);
        await frame.evaluate(() => handoffAttention.refresh());
        assert.equal(mobileMessage.unread, true, 'Mobile list is not a visible chat');
        await frame.locator('[data-conversation-id="1"]').click();
        await frame.waitForFunction(id => acknowledged.get(1) >= id, mobileMessage.id);
        assert.equal(mobileMessage.unread, false);
        assert.equal(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);

        // A response for the previous selection must never overwrite or acknowledge the next chat.
        await page.setViewportSize({width: 1440, height: 1000});
        delayChat = 2;
        await frame.evaluate(() => { openConversation(2, 'messenger', 'sender2'); });
        await page.waitForFunction(() => true);
        while (!releaseDelayed) await new Promise(resolve => setTimeout(resolve, 10));
        await frame.evaluate(() => openConversation(3, 'messenger', 'sender3'));
        releaseDelayed();
        await ready(frame);
        assert.equal(await frame.locator('#current-conv-id').innerText(), '3');
        assert.equal((await frame.locator('#chat-messages').innerText()).includes('history 2'), false);

        const pending = addMessage(1);
        await page.reload({waitUntil: 'networkidle'});
        await page.evaluate(() => showSection('handoff'));
        const reloaded = await (await page.locator('#iframe-handoff').elementHandle()).contentFrame();
        await reloaded.locator('[data-conversation-id="1"] .unread-badge').waitFor();
        assert.equal(pending.unread, true, 'Unread survives page reload');

        // Standalone page uses the same poller, and background tabs do not read.
        await page.goto(base + '/api/handoff/', {waitUntil: 'networkidle'});
        await page.locator('[data-conversation-id="1"]').click();
        await page.waitForFunction(id => acknowledged.get(1) >= id, pending.id);
        // Headless Chrome reports every tab as visible/focused, so drive the browser
        // visibility signals explicitly while running the real application code.
        await page.evaluate(() => {
            Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
            Object.defineProperty(document, 'hasFocus', {configurable: true, value: () => false});
            document.dispatchEvent(new Event('visibilitychange'));
        });
        const backgroundMessage = addMessage(1);
        await page.evaluate(() => handoffAttention.refresh());
        assert.equal(backgroundMessage.unread, true, 'Background tab does not acknowledge');
        await page.evaluate(() => {
            delete document.visibilityState;
            delete document.hasFocus;
            document.dispatchEvent(new Event('visibilitychange'));
        });
        await page.evaluate(() => handoffAttention.refresh());
        assert.equal(backgroundMessage.unread, false);
        const beforeBell = await page.evaluate(() => bellCount);
        addMessage(3);
        await page.evaluate(() => handoffNotifier.poll());
        assert.equal(await page.evaluate(() => bellCount), beforeBell + 1, 'Standalone sound works');
        assert.deepEqual(errors, []);
        console.log('PASS: handoff ordering, unread persistence, bounded viewing, hidden sections/tabs, mobile, stale responses, safe text, and notification sound');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
