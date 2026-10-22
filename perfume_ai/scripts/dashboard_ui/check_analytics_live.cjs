const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const base = 'http://127.0.0.1:8767';
const out = process.env.DASHBOARD_UI_ARTIFACTS || path.join(require('node:os').tmpdir(), 'webvitas-dashboard-review');
(async () => {
    const browser = await chromium.launch({ channel: 'chrome', headless: true });
    try {
        const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
        await context.route('**/*', route => {
            const url = new URL(route.request().url());
            return url.origin === base || ['fonts.googleapis.com', 'fonts.gstatic.com', 'cdnjs.cloudflare.com', 'cdn.jsdelivr.net'].includes(url.hostname) ? route.continue() : route.abort();
        });
        const login = await context.request.post(base + '/dashboard/api/login/', { data: { email: 'analytics1@example.test', password: 'browser-test-only' } });
        assert.equal(login.status(), 200);
        const tokens = await login.json();
        await context.addInitScript(tokens => { localStorage.setItem('access_token', tokens.access); localStorage.setItem('refresh_token', tokens.refresh); }, tokens);
        const page = await context.newPage(), errors = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
        await page.goto(base + '/dashboard/?section=analytics');
        await page.waitForFunction(() => !document.getElementById('analytics-export').disabled);
        const headers = { Authorization: 'Bearer ' + tokens.access };
        const api = await context.request.get(base + '/dashboard/api/analytics/?version=2&store_id=2', { headers });
        const report = await api.json();
        assert.equal(report.kpis.revenue, '300.00'); assert.equal(report.kpis.orders, 3);
        assert.equal(report.kpis.conversion, '66.7'); assert.equal(report.products.total, 1);
        assert.equal(await page.locator('[data-metric=orders] .kpi-value').innerText(), '٣');
        assert.ok((await page.locator('[data-metric=revenue] .kpi-value').innerText()).includes('٣٠٠'));
        assert.equal(await page.locator('#analytics-products-body').getByText('عود الاختبار').count(), 1);
        assert.equal(await page.getByText('FOREIGN-STORE-PRODUCT').count(), 0);
        await page.locator('#analytics-aggregation').selectOption('weekly');
        await page.waitForFunction(() => !document.getElementById('analytics-export').disabled);
        await page.locator('#analytics-report').selectOption('products');
        const event = page.waitForEvent('download'); await page.locator('#analytics-export').click(); const download = await event;
        const csv = fs.readFileSync(await download.path(), 'utf8');
        assert.ok(csv.includes('عود الاختبار')); assert.ok(csv.includes('300')); assert.ok(!csv.includes('FOREIGN'));
        for (const width of [1440, 375]) {
            await page.setViewportSize({ width, height: 1000 });
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
            await page.screenshot({ path: path.join(out, `analytics-real-api-${width}.png`), fullPage: true });
        }
        const other = await context.request.post(base + '/dashboard/api/login/', { data: { email: 'analytics2@example.test', password: 'browser-test-only' } });
        const otherTokens = await other.json();
        const otherReport = await (await context.request.get(base + '/dashboard/api/analytics/?version=2', { headers: { Authorization: 'Bearer ' + otherTokens.access } })).json();
        assert.equal(otherReport.kpis.revenue, '3000.00');
        assert.equal(otherReport.products.rows[0].name, 'FOREIGN-STORE-PRODUCT');
        assert.deepEqual(errors, []);
        const result = { passed: ['Actual login, real database aggregation, rendered metrics, weekly charts, CSV download, responsive layout and two-store isolation'], errors };
        fs.writeFileSync(path.join(out, 'analytics-real-api-report.json'), JSON.stringify(result, null, 2));
        console.log(JSON.stringify(result));
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
