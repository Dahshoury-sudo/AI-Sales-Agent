// Run against preview.py. All settings requests are mocked; no live API is used.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {intercept, base, out} = require('./check_layout.cjs');
const assert = require('node:assert/strict');
const path = require('node:path');

// [settings field, element id, accessible name], in tab order.
const SWITCHES = [
    ['whatsapp_replies_enabled', 'whatsapp-replies-enabled', 'رسائل واتساب'],
    ['messenger_replies_enabled', 'messenger-replies-enabled', 'رسائل ماسنجر'],
    ['instagram_replies_enabled', 'instagram-replies-enabled', 'رسائل إنستجرام'],
    ['tiktok_replies_enabled', 'tiktok-replies-enabled', 'رسائل تيك توك'],
    ['facebook_comment_replies_enabled', 'facebook-comment-replies-enabled', 'تعليقات فيسبوك'],
    ['instagram_comment_replies_enabled', 'instagram-comment-replies-enabled', 'تعليقات إنستجرام'],
];
const FIRST_ID = SWITCHES[0][1];

(async () => {
    const browser = await chromium.launch({channel: 'chrome', headless: true});
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
        await context.route('**/*', intercept);
        const settings = Object.fromEntries(SWITCHES.map(([field]) => [field, true]));
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
        const switches = SWITCHES.map(([, , name]) => page.getByRole('switch', {name, exact: true}));
        const ready = () => page.waitForFunction(id => !document.getElementById(id).disabled, FIRST_ID);
        const states = () => Promise.all(switches.map(control => control.isChecked()));

        await page.goto(base + '/dashboard/', {waitUntil: 'networkidle'});
        await page.evaluate(() => showSection('meta'));
        for (const control of switches) assert.equal(await control.isDisabled(), true);
        releaseRead(); holdRead = null;
        await ready();
        assert.deepEqual(await states(), SWITCHES.map(() => true));

        // First switch: pending state locks every switch and sends only its own field.
        holdWrite = new Promise(resolve => { releaseWrite = resolve; });
        await switches[0].focus();
        await page.keyboard.press('Space');
        await page.waitForFunction(() => document.getElementById('reply-controls').getAttribute('aria-busy') === 'true');
        for (const control of switches) assert.equal(await control.isDisabled(), true);
        assert.deepEqual(writes, [{[SWITCHES[0][0]]: false}]);
        releaseWrite(); holdWrite = null;
        await ready();
        assert.deepEqual(await states(), SWITCHES.map((_, j) => j !== 0));
        assert.equal(await switches[0].evaluate(el => document.activeElement === el), true);

        // Tab through the rest: each one saves only itself and leaves the others as they were.
        for (let i = 1; i < SWITCHES.length; i++) {
            await page.keyboard.press('Tab');
            assert.equal(await switches[i].evaluate(el => document.activeElement === el), true, `tab to ${SWITCHES[i][2]}`);
            await page.keyboard.press('Space');
            await ready();
            assert.deepEqual(writes.at(-1), {[SWITCHES[i][0]]: false});
            assert.deepEqual(await states(), SWITCHES.map((_, j) => j > i));
            assert.equal(await switches[i].evaluate(el => document.activeElement === el), true);
        }

        failWrite = true;
        await switches[0].click();
        await page.waitForSelector('#reply-controls-status.is-error');
        assert.deepEqual(await states(), SWITCHES.map(() => false));
        assert.equal(await switches[0].isDisabled(), false);
        failWrite = false;

        failRead = true;
        await page.evaluate(() => window.ReplyControls.load());
        assert.equal(await switches[0].isDisabled(), true);
        assert.equal(await page.locator('#reply-controls-retry').isVisible(), true);
        failRead = false;
        await page.locator('#reply-controls-retry').click();
        await ready();
        assert.deepEqual(await states(), SWITCHES.map(() => false));

        // Reload persists every state, and the separate credentials save omits all switches.
        await switches[2].click();
        await ready();
        await page.reload({waitUntil: 'networkidle'});
        await page.evaluate(() => showSection('meta'));
        await ready();
        assert.deepEqual(await states(), SWITCHES.map((_, j) => j === 2));
        await page.evaluate(() => saveMetaSettings());
        for (const [field] of SWITCHES) assert.equal(field in writes.at(-1), false);

        for (const width of [1440, 768, 375]) {
            await page.setViewportSize({width, height: 1000});
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
            for (const control of switches) {
                const box = await control.boundingBox();
                assert.ok(box.width >= 44 && box.height >= 28);
            }
            await page.screenshot({path: path.join(out, `reply-controls-${width}.png`), fullPage: true});
        }
        assert.deepEqual(errors, []);
        console.log(`PASS: ${SWITCHES.length} per-platform switches: loading, keyboard order, independent autosave, pending state, failure recovery, reload, credentials isolation, and 3 responsive layouts`);
        console.log('Screenshots: ' + out);
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
