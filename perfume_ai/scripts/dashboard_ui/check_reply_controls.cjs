// Run against preview.py. All settings requests are mocked; no live API is used.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {intercept, base, out} = require('./check_layout.cjs');
const assert = require('node:assert/strict');
const path = require('node:path');

(async () => {
    const browser = await chromium.launch({channel: 'chrome', headless: true});
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
        await context.route('**/*', intercept);
        const settings = {message_replies_enabled: true, comment_replies_enabled: true};
        const writes = [];
        let failRead = false, failWrite = false, releaseRead, releaseWrite;
        let holdRead = new Promise(resolve => { releaseRead = resolve; });
        let holdWrite = null;
        await context.route('**/dashboard/api/settings/', async route => {
            const write = route.request().method() === 'PUT';
            if (write) {
                writes.push(route.request().postDataJSON());
                if (holdWrite) await holdWrite;
                if (!failWrite) Object.assign(settings, writes.at(-1));
            } else if (holdRead) await holdRead;
            await route.fulfill({status: (write ? failWrite : failRead) ? 500 : 200,
                contentType: 'application/json', body: JSON.stringify(write ? {message: 'Saved'} : settings)});
        });
        await context.addInitScript(() => {
            localStorage.setItem('access_token', 'preview');
            localStorage.setItem('refresh_token', 'preview-refresh');
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base + '/dashboard/', {waitUntil: 'networkidle'});
        await page.evaluate(() => showSection('meta'));
        const messages = page.getByRole('switch', {name: 'الرد على الرسائل', exact: true});
        const comments = page.getByRole('switch', {name: 'الرد على التعليقات', exact: true});
        assert.equal(await messages.isDisabled(), true);
        assert.equal(await comments.isDisabled(), true);
        releaseRead(); holdRead = null;
        await page.waitForFunction(() => !document.getElementById('message-replies-enabled').disabled);
        assert.equal(await messages.isChecked(), true);
        assert.equal(await comments.isChecked(), true);

        holdWrite = new Promise(resolve => { releaseWrite = resolve; });
        await messages.focus();
        await page.keyboard.press('Space');
        await page.waitForFunction(() => document.getElementById('reply-controls').getAttribute('aria-busy') === 'true');
        assert.equal(await messages.isDisabled(), true);
        assert.equal(await comments.isDisabled(), true);
        assert.deepEqual(writes, [{message_replies_enabled: false}]);
        releaseWrite(); holdWrite = null;
        await page.waitForFunction(() => !document.getElementById('message-replies-enabled').disabled);
        assert.equal(await messages.isChecked(), false);
        assert.equal(await comments.isChecked(), true);
        assert.equal(await messages.evaluate(el => document.activeElement === el), true);

        await page.keyboard.press('Tab');
        assert.equal(await comments.evaluate(el => document.activeElement === el), true);
        await page.keyboard.press('Space');
        await page.waitForFunction(() => !document.getElementById('comment-replies-enabled').disabled);
        assert.equal(await comments.isChecked(), false);
        assert.deepEqual(writes.at(-1), {comment_replies_enabled: false});

        failWrite = true;
        await messages.click();
        await page.waitForSelector('#reply-controls-status.is-error');
        assert.equal(await messages.isChecked(), false);
        assert.equal(await comments.isChecked(), false);
        assert.equal(await messages.isDisabled(), false);
        failWrite = false;

        failRead = true;
        await page.evaluate(() => window.ReplyControls.load());
        assert.equal(await messages.isDisabled(), true);
        assert.equal(await page.locator('#reply-controls-retry').isVisible(), true);
        failRead = false;
        await page.locator('#reply-controls-retry').click();
        await page.waitForFunction(() => !document.getElementById('message-replies-enabled').disabled);
        assert.equal(await messages.isChecked(), false);

        // Reload persists both states, and the separate credentials save omits them.
        await page.reload({waitUntil: 'networkidle'});
        await page.evaluate(() => showSection('meta'));
        await page.waitForFunction(() => !document.getElementById('message-replies-enabled').disabled);
        assert.equal(await messages.isChecked(), false);
        assert.equal(await comments.isChecked(), false);
        await page.evaluate(() => saveMetaSettings());
        assert.equal('message_replies_enabled' in writes.at(-1), false);
        assert.equal('comment_replies_enabled' in writes.at(-1), false);

        for (const width of [1440, 768, 375]) {
            await page.setViewportSize({width, height: 1000});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
            for (const control of [messages, comments]) {
                const box = await control.boundingBox();
                assert.ok(box.width >= 44 && box.height >= 28);
            }
            await page.screenshot({path: path.join(out, `reply-controls-${width}.png`), fullPage: true});
        }
        assert.deepEqual(errors, []);
        console.log('PASS: loading, keyboard, independent autosave, pending state, failure recovery, reload, credentials isolation, and 3 responsive layouts');
        console.log('Screenshots: ' + out);
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
