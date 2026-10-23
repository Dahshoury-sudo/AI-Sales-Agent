/* Real browser auth/polling code, synthetic APIs and clock; no business services. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const {base, intercept} = require('./check_layout.cjs');
const jwt = exp => 'test.' + Buffer.from(JSON.stringify({exp})).toString('base64url') + '.signature';
const valid = jwt(Math.floor(Date.now() / 1000) + 43200);
const expired = jwt(Math.floor(Date.now() / 1000) - 60);
const harness = '<!doctype html><script src="/static/dashboard/polling.js"></script><script src="/static/dashboard/auth.js"></script>';

(async () => {
    const browser = await chromium.launch({channel: 'chrome', headless: true});
    const passed = [];
    async function setup(access = expired) {
        const context = await browser.newContext();
        await context.route('**/*', intercept);
        await context.route('**/request-test*', route => route.fulfill({contentType: 'text/html', body: harness}));
        await context.addInitScript(() => {
            const realNow = Date.now;
            window.testOffset = 0;
            Date.now = () => realNow() + window.testOffset;
            window.testTimers = [];
            window.setInterval = fn => { testTimers.push(fn); return testTimers.length; };
            window.clearInterval = () => {};
        });
        const page = await context.newPage();
        await page.goto(base + '/request-test');
        await page.evaluate(access => {
            localStorage.setItem('access_token', access);
            localStorage.setItem('refresh_token', 'refresh-original');
        }, access);
        return {context, page};
    }
    try {
        {
            const {context, page} = await setup();
            let refreshes = 0;
            await context.route('**/dashboard/api/token/refresh/', async route => {
                refreshes++;
                await new Promise(resolve => setTimeout(resolve, 40));
                await route.fulfill({json: {access: valid, refresh: 'refresh-rotated'}});
            });
            const tab = await context.newPage();
            await tab.goto(base + '/request-test-tab');
            await page.evaluate(() => {
                const frame = document.createElement('iframe'); frame.src = '/request-test-frame'; document.body.append(frame);
            });
            const frame = page.frame({url: base + '/request-test-frame'}) || await new Promise(resolve => page.once('framenavigated', resolve));
            await frame.waitForFunction(() => !!window.WebVitasAuth);
            const results = await Promise.all([
                page.evaluate(() => Promise.all([authFetch('/api/history/conversations/'), authFetch('/api/handoff/conversations/1/messages/')]).then(rows => rows.map(r => r.status))),
                frame.evaluate(() => authFetch('/api/orders/dashboard/').then(r => r.status)),
                tab.evaluate(() => authFetch('/dashboard/api/profile/').then(r => r.status)),
            ]);
            assert.deepEqual(results, [[200, 200], 200, 200]);
            assert.equal(refreshes, 1, 'One refresh across requests, frames and tabs');
            assert.deepEqual(await page.evaluate(() => [localStorage.getItem('access_token'), localStorage.getItem('refresh_token')]), [valid, 'refresh-rotated']);
            // The parent helper must recognize FormData constructed in an iframe.
            let upload;
            await context.route('**/api/import/upload/', route => {
                upload = route.request(); return route.fulfill({json: {created: 1}});
            });
            assert.equal(await frame.evaluate(async () => {
                const data = new FormData(); data.append('file', new File(['sheet'], 'products.xlsx'));
                return (await authFetch('/api/import/upload/', {method: 'POST', body: data})).status;
            }), 200);
            assert.match(upload.headers()['content-type'], /^multipart\/form-data; boundary=/);
            assert.match(upload.postData(), /products.xlsx/);
            assert.equal(upload.headers().authorization, 'Bearer ' + valid);
            passed.push('Expired history/messages, concurrent frames/tabs, rotated tokens and iframe uploads');
            await context.close();
        }
        {
            const {context, page} = await setup('old-but-unexpired');
            let refreshes = 0;
            const writes = [];
            await context.route('**/dashboard/api/token/refresh/', route => {
                refreshes++; return route.fulfill({json: {access: valid, refresh: 'new-refresh'}});
            });
            await context.route('**/api/handoff/conversations/1/reply/', route => {
                writes.push(route.request());
                return route.request().headers().authorization === 'Bearer ' + valid
                    ? route.fulfill({json: {status: 'sent'}}) : route.fulfill({status: 401, json: {}});
            });
            const status = await page.evaluate(async () => (await authFetch('/api/handoff/conversations/1/reply/', {
                method: 'POST', headers: new Headers({'X-Test': 'preserved'}), body: JSON.stringify({message: 'hello'}),
            })).status);
            assert.equal(status, 200); assert.equal(refreshes, 1); assert.equal(writes.length, 2);
            assert.equal(writes[0].postData(), writes[1].postData());
            assert.equal(writes[1].headers()['x-test'], 'preserved');
            const aborted = await page.evaluate(async () => {
                const controller = new AbortController(); controller.abort();
                try { await authFetch('/dashboard/api/profile/', {signal: controller.signal}); }
                catch (error) { return error.name; }
            });
            assert.equal(aborted, 'AbortError');
            passed.push('Unexpected 401 retries once, preserves write payloads/headers and cancellation');
            await context.close();
        }
        for (const failure of [429, 503, 'network']) {
            const {context, page} = await setup();
            let attempts = 0;
            await context.route('**/dashboard/api/token/refresh/', route => {
                attempts++;
                if (attempts > 1) return route.fulfill({json: {access: valid, refresh: 'recovered'}});
                return failure === 'network' ? route.abort() : route.fulfill({status: failure, headers: {'Retry-After': '30'}, json: {}});
            });
            await page.evaluate(async () => {
                await authFetch('/dashboard/api/profile/').catch(() => {});
                await authFetch('/dashboard/api/profile/').catch(() => {});
            });
            assert.equal(attempts, 1);
            assert.equal(await page.evaluate(() => localStorage.getItem('refresh_token')), 'refresh-original');
            assert.equal(page.url(), base + '/request-test');
            await page.evaluate(() => { testOffset += 31000; });
            assert.equal(await page.evaluate(() => authFetch('/dashboard/api/profile/').then(r => r.status)), 200);
            assert.equal(attempts, 2);
            await context.close();
        }
        passed.push('Refresh 429, server and network failures preserve sessions and respect cooldown');
        {
            const {context, page} = await setup(valid);
            let refreshes = 0;
            const headers = [];
            await context.route('**/dashboard/api/token/refresh/', route => {
                refreshes++; return route.fulfill({json: {access: valid, refresh: 'history-refresh'}});
            });
            await context.route('**/api/history/conversations/', route => {
                headers.push(route.request().headers().authorization); return intercept(route);
            });
            await context.route('**/api/handoff/conversations/1/messages/', route => {
                headers.push(route.request().headers().authorization); return intercept(route);
            });
            await page.goto(base + '/api/history/');
            await page.locator('.conv-item').first().click();
            await page.waitForFunction(() => document.getElementById('chat-messages').children.length > 1);
            headers.length = 0;
            await page.evaluate(expired => { localStorage.setItem('access_token', expired); }, expired);
            await page.evaluate(() => Promise.all(testTimers.map(fn => fn())));
            assert.equal(refreshes, 1, 'The real history page refreshes its stale login');
            assert.ok(headers.length >= 2);
            assert.ok(headers.every(header => header === 'Bearer ' + valid));
            passed.push('Real history page refreshes both conversation and selected-message polling');
            await context.close();
        }
        {
            const {context, page} = await setup();
            let refreshes = 0;
            await context.route('**/dashboard/api/token/refresh/', route => {
                refreshes++; return route.fulfill({status: 401, json: {detail: 'Token expired'}});
            });
            await page.evaluate(() => {
                authFetch('/api/history/conversations/'); authFetch('/api/handoff/conversations/1/messages/');
            }).catch(() => {});
            await page.waitForURL('**/dashboard/login/');
            assert.equal(refreshes, 1);
            assert.equal(await page.evaluate(() => localStorage.getItem('access_token')), null);
            passed.push('Rejected refresh redirects once and clears expired credentials');
            await context.close();
        }
        {
            const {context, page} = await setup('rejected-access');
            let refreshes = 0, requests = 0;
            await context.route('**/dashboard/api/token/refresh/', route => {
                refreshes++; return route.fulfill({json: {access: valid, refresh: 'rotated'}});
            });
            await context.route('**/dashboard/api/profile/', route => {
                requests++; return route.fulfill({status: 401, json: {}});
            });
            await page.evaluate(() => { authFetch('/dashboard/api/profile/'); });
            await page.waitForURL('**/dashboard/login/');
            assert.equal(refreshes, 1); assert.equal(requests, 2, 'No infinite refresh after a second 401');
            await context.close();
            const missing = await setup(expired);
            await missing.page.evaluate(() => { localStorage.removeItem('refresh_token'); authFetch('/api/history/conversations/'); });
            await missing.page.waitForURL('**/dashboard/login/');
            await missing.context.close();
            passed.push('Missing refresh and repeated 401 stop polling instead of looping');
        }
        {
            const {context, page} = await setup(valid);
            let gets = 0;
            await context.route('**/api/history/conversations/', async route => {
                gets++;
                await new Promise(resolve => setTimeout(resolve, 20));
                return gets === 1 ? route.fulfill({status: 429, headers: {'Retry-After': '30'}, json: {}})
                    : route.fulfill({json: {conversations: []}});
            });
            await page.evaluate(async () => {
                await Promise.all([authFetch('/api/history/conversations/'), authFetch('/api/history/conversations/')]);
                await authFetch('/api/history/conversations/');
                testOffset += 29000;
                document.dispatchEvent(new Event('visibilitychange'));
                await authFetch('/api/history/conversations/');
            });
            assert.equal(gets, 1, 'No overlap or early cooldown retry');
            await page.evaluate(() => { testOffset += 2000; });
            assert.equal(await page.evaluate(() => authFetch('/api/history/conversations/').then(r => r.status)), 200);
            assert.equal(gets, 2);
            const visibility = await page.evaluate(async () => {
                let reads = 0, notifications = 0;
                const reader = WebVitasPolling.watch(() => { reads++; }, 10000);
                const notifier = WebVitasPolling.watch(() => { notifications++; }, 10000, {background: true});
                await reader.refresh(); await notifier.refresh();
                const before = [reads, notifications];
                Object.defineProperty(document, 'hidden', {configurable: true, value: true});
                await reader.refresh(); await notifier.refresh();
                const hidden = [reads, notifications];
                delete document.hidden;
                await reader.refresh();
                reader.stop(); notifier.stop();
                return {before, hidden, resumed: reads};
            });
            assert.equal(visibility.hidden[0], visibility.before[0]);
            assert.equal(visibility.hidden[1], visibility.before[1] + 1);
            assert.equal(visibility.resumed, visibility.before[0] + 1);
            passed.push('Polling coalesces duplicate reads, honors Retry-After and preserves background notifications');
            await context.close();
        }
        console.log(JSON.stringify({passed}, null, 2));
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
