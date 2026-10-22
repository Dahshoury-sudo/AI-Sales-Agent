const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { intercept, base, out } = require('./check_layout.cjs');
const { fixture } = require('./analytics_fixture.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
    const browser = await chromium.launch({ channel: 'chrome', headless: true });
    const passed = [];
    try {
        const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
        await context.route('**/*', intercept);
        await context.addInitScript(() => { localStorage.setItem('access_token', 'preview'); localStorage.setItem('refresh_token', 'preview-refresh'); });
        const page = await context.newPage(), errors = [], requests = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('request', request => { if (request.url().includes('/analytics/')) requests.push(new URL(request.url())); });
        const ready = () => page.waitForFunction(() => document.getElementById('analytics-content').getAttribute('aria-busy') === 'false' && !document.getElementById('analytics-export').disabled);
        await page.goto(base + '/dashboard/?section=analytics'); await ready();
        assert.equal(await page.locator('#analytics-preset').inputValue(), '30d');
        assert.equal(await page.locator('#analytics-kpis-grid article').count(), 6);
        assert.equal(await page.locator('#analytics-products-body tr').count(), 20);
        await page.locator('#analytics-next').click(); await ready();
        assert.equal(await page.locator('#analytics-products-body tr').count(), 4);
        await page.locator('#analytics-previous').click(); await ready();
        await page.locator('[data-analytics-sort=units]').click(); await ready();
        assert.equal(await page.locator('[data-analytics-sort=units]').locator('..').getAttribute('aria-sort'), 'ascending');
        await page.locator('#analytics-search').fill('Oud');
        await page.waitForFunction(() => document.querySelectorAll('#analytics-products-body tr').length === 1 && document.querySelector('#analytics-products-body').textContent.includes('Oud'));
        await ready(); assert.equal(new URL(page.url()).searchParams.get('search'), 'Oud');
        await page.locator('#analytics-report').selectOption('products');
        const downloadEvent = page.waitForEvent('download'); await page.locator('#analytics-export').click(); const download = await downloadEvent;
        assert.match(download.suggestedFilename(), /analytics-products/);
        const exported = requests.filter(u => u.pathname.endsWith('/export/')).at(-1);
        assert.equal(exported.searchParams.get('search'), 'Oud'); assert.ok(exported.searchParams.has('as_of'));
        passed.push('Default range, product search/sort/pagination, filtered CSV download');

        for (const preset of ['today', '7d', 'month', '30d']) {
            await page.locator('#analytics-preset').selectOption(preset); await ready();
            assert.equal(new URL(page.url()).searchParams.get('preset'), preset);
        }
        for (const aggregation of ['weekly', 'monthly', 'daily']) { await page.locator('#analytics-aggregation').selectOption(aggregation); await ready(); assert.equal(requests.filter(u => u.pathname.endsWith('/analytics/')).at(-1).searchParams.get('aggregation'), aggregation); }
        await page.locator('#analytics-preset').selectOption('custom');
        assert.equal(await page.locator('#analytics-export').isDisabled(), true);
        await page.locator('#analytics-start').fill('2026-10-01'); await page.locator('#analytics-end').fill('2026-10-05');
        await page.locator('#analytics-custom button').click(); await ready();
        const before = page.url(); await page.reload(); await ready(); assert.equal(page.url(), before);
        await page.locator('#analytics-preset').selectOption('today'); await ready();
        await page.goBack(); await ready(); assert.equal(await page.locator('#analytics-preset').inputValue(), 'custom');
        await page.goForward(); await ready(); assert.equal(await page.locator('#analytics-preset').inputValue(), 'today');
        const n = requests.length; await page.locator('#analytics-refresh').click(); await ready(); assert.ok(requests.length > n);
        passed.push('Every preset/aggregation, custom apply, refresh, URL reload and browser history');

        let delayed;
        await page.route('**/dashboard/api/analytics/?*', async route => {
            if (new URL(route.request().url()).searchParams.get('preset') === '7d') { delayed = route; return; }
            await route.fulfill({ contentType: 'application/json', body: JSON.stringify(fixture(route.request().url())) });
        });
        await page.locator('#analytics-preset').selectOption('7d');
        await page.waitForFunction(() => document.getElementById('analytics-content').getAttribute('aria-busy') === 'true');
        while (!delayed) await page.waitForTimeout(10);
        await page.locator('#analytics-preset').selectOption('today'); await ready();
        await delayed.fulfill({ contentType: 'application/json', body: JSON.stringify(fixture(delayed.request().url())) }).catch(() => {});
        await page.waitForTimeout(100);
        assert.equal(await page.locator('[data-metric=orders] .kpi-value').innerText(), '١');
        await page.unroute('**/dashboard/api/analytics/?*');

        let staleTable;
        await page.route('**/dashboard/api/analytics/products/?*', async route => {
            const search = new URL(route.request().url()).searchParams.get('search');
            if (search === 'عطر') { staleTable = route; return; }
            await route.fulfill({ contentType: 'application/json', body: JSON.stringify(fixture(route.request().url()).products) });
        });
        await page.locator('#analytics-search').fill('عطر');
        while (!staleTable) await page.waitForTimeout(25);
        await page.locator('#analytics-search').fill('no-results'); await ready();
        await staleTable.fulfill({ contentType: 'application/json', body: JSON.stringify(fixture(staleTable.request().url()).products) }).catch(() => {});
        await page.waitForTimeout(100); assert.match(await page.locator('#analytics-products-body').innerText(), /لا توجد منتجات تطابق/);
        await page.unroute('**/dashboard/api/analytics/products/?*');
        passed.push('Superseded report and product responses cannot overwrite newer selections');

        await page.route('**/dashboard/api/analytics/?*', route => route.fulfill({ status: 503, contentType: 'text/plain', body: 'Unavailable' }));
        await page.locator('#analytics-refresh').click(); await page.locator('#analytics-status.is-error').waitFor();
        assert.equal(await page.locator('#analytics-export').isDisabled(), true);
        await page.unroute('**/dashboard/api/analytics/?*'); await page.locator('#analytics-retry').click(); await ready();
        await page.route('**/dashboard/api/analytics/?*', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(fixture(route.request().url(), true)) }));
        await page.locator('#analytics-refresh').click(); await ready(); await page.locator('#analytics-empty').waitFor();
        assert.equal(await page.locator('[data-metric=conversion] .kpi-value').innerText(), '—');
        await page.unroute('**/dashboard/api/analytics/?*');
        await page.evaluate(() => { window.Chart = undefined; });
        await page.locator('#analytics-refresh').click(); await ready();
        assert.match(await page.locator('#analytics-status').innerText(), /الجداول/);
        await page.locator('#analytics-revenue-table').locator('..').locator('summary').focus(); await page.keyboard.press('Enter');
        assert.equal(await page.locator('#analytics-revenue-table table').isVisible(), true);
        passed.push('Failure/retry, stale export protection, empty data, keyboard table alternatives and Chart.js failure');
        await page.reload(); await ready();
        await page.locator('#analytics-search').fill(''); await ready();
        for (const width of [1440, 768, 375]) {
            await page.setViewportSize({ width, height: 1000 });
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
            await page.screenshot({ path: path.join(out, `analytics-verified-${width}.png`), fullPage: true });
        }
        const axe = await fetch('https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.3/axe.min.js').then(r => r.text());
        await page.addScriptTag({ content: axe });
        const violations = await page.evaluate(async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } })).violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })));
        assert.deepEqual(violations, []); assert.deepEqual(errors, []);
        passed.push('Responsive screenshots, mobile WCAG checks and no runtime errors');
        fs.writeFileSync(path.join(out, 'analytics-report.json'), JSON.stringify({ passed }, null, 2));
        console.log(JSON.stringify({ passed }, null, 2));
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
