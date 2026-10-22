/* Store analytics. All business calculations live in the reporting service. */
(() => {
    'use strict';
    const root = document.getElementById('section-analytics');
    if (!root) return;
    const el = id => document.getElementById('analytics-' + id);
    const labels = { revenue: 'الإيرادات', orders: 'الطلبات', aov: 'متوسط قيمة الطلب', customers: 'العملاء الفريدون', conversations: 'المحادثات الجديدة', conversion: 'التحويل إلى طلب' };
    const platforms = { web: 'الموقع', whatsapp: 'واتساب', messenger: 'ماسنجر', instagram: 'إنستجرام', facebook: 'تعليقات فيسبوك', unknown: 'منصة غير معروفة', unattributed: 'غير منسوب' };
    const statuses = { pending: 'قيد الانتظار', confirmed: 'مؤكد', delivered: 'تم التسليم', cancelled: 'ملغي', unknown: 'غير معروف' };
    const keys = ['preset', 'start', 'end', 'aggregation', 'search', 'sort', 'page', 'report'];
    const defaults = { preset: '30d', start: '', end: '', aggregation: 'daily', search: '', sort: '-revenue', page: '1', report: 'overview' };
    const count = value => value == null ? '—' : new Intl.NumberFormat('ar-EG', { maximumFractionDigits: 1 }).format(Number(value));
    const currency = value => value == null ? '—' : new Intl.NumberFormat('ar-EG', { style: 'currency', currency: 'EGP', maximumFractionDigits: 2 }).format(Number(value));
    const date = value => new Intl.DateTimeFormat('ar-EG', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(value + 'T12:00:00Z'));
    const range = (start, end) => start === end ? date(start) : `${date(start)} – ${date(end)}`;
    const metric = (key, value) => ['revenue', 'aov'].includes(key) ? currency(value) : key === 'conversion' ? value == null ? '—' : count(value) + '٪' : count(value);
    const node = (tag, text, className) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (className) n.className = className; return n; };
    let state = readURL(), data = null, committed = null, mainController, tableController, exportController;
    let generation = 0, tableGeneration = 0, busy = false, mainDirty = true, tableBusy = false, failed = false, draft = false, exporting = false, restoring = false, searchTimer;
    const charts = new Map();

    function readURL() {
        const params = new URLSearchParams(location.search);
        return Object.fromEntries(keys.map(key => [key, params.get(key) ?? defaults[key]]));
    }
    function writeURL(push = true, section = document.body.dataset.section || 'analytics') {
        const url = new URL(location.href);
        url.searchParams.set('section', section);
        keys.forEach(key => {
            const value = state[key];
            if (value && (!['start', 'end'].includes(key) || state.preset === 'custom')) url.searchParams.set(key, value);
            else url.searchParams.delete(key);
        });
        if (url.href !== location.href) history[push ? 'pushState' : 'replaceState']({}, '', url);
    }
    function syncControls() {
        keys.filter(key => el(key)).forEach(key => { el(key).value = state[key]; });
        el('custom').hidden = state.preset !== 'custom';
        el('start').required = el('end').required = state.preset === 'custom';
    }
    function params(selection = state, cutoff) {
        const p = new URLSearchParams({ version: '2', preset: selection.preset, aggregation: selection.aggregation, search: selection.search, sort: selection.sort, page: selection.page, report: selection.report });
        if (selection.preset === 'custom') { p.set('start', selection.start); p.set('end', selection.end); }
        if (cutoff) p.set('as_of', cutoff);
        return p;
    }
    function controls() {
        el('export').disabled = !committed || busy || tableBusy || failed || draft || exporting;
        el('refresh').disabled = busy;
        el('content').setAttribute('aria-busy', String(busy));
    }
    function status(text, error = false) {
        el('status-text').textContent = text;
        el('status').hidden = !text;
        el('status').classList.toggle('is-error', error);
        el('retry').hidden = !error;
    }
    async function responseJSON(response) {
        if (!response) throw new Error('انتهت جلسة الدخول. سجّل الدخول مرة أخرى.');
        if (!response.ok) {
            if (response.status === 400) {
                const details = await response.json().catch(() => ({}));
                throw new Error(Object.values(details).flat().join(' ') || 'راجع اختيارات التقرير.');
            }
            throw new Error('تعذّر تحميل التقرير. تحقق من الاتصال وأعد المحاولة.');
        }
        return response.json();
    }
    async function load() {
        clearTimeout(searchTimer);
        const id = ++generation;
        ++tableGeneration;
        mainController?.abort(); tableController?.abort(); exportController?.abort();
        mainController = new AbortController();
        busy = true; mainDirty = true; tableBusy = false; failed = false; draft = false; controls();
        status(data ? 'جاري التحديث… الأرقام المعروضة من آخر تحميل ناجح.' : 'جاري تحميل بيانات المتجر…');
        const selection = { ...state };
        try {
            const response = await window.authFetch('/dashboard/api/analytics/?' + params(selection), { signal: mainController.signal, suppressError: true });
            const result = await responseJSON(response);
            if (id !== generation) return;
            data = result; committed = selection; mainDirty = false;
            render(result);
            state.page = String(result.products.page); writeURL(false);
            status(window.Chart ? '' : 'الرسم غير متاح حالياً. كل البيانات متاحة في الجداول أسفل الرسوم.');
        } catch (error) {
            if (id !== generation || error.name === 'AbortError') return;
            failed = true;
            status(error.message + (data ? ' الأرقام المعروضة قديمة ولا تطابق بالضرورة الاختيارات الحالية.' : ''), true);
        } finally {
            if (id === generation) { busy = false; controls(); }
        }
    }
    async function loadProducts() {
        if (!data || busy || mainDirty || failed || draft) { if (!draft) load(); return; }
        const id = ++tableGeneration, parent = generation;
        tableController?.abort(); exportController?.abort();
        tableController = new AbortController(); tableBusy = true; controls();
        el('product-status').textContent = 'جاري تحديث المنتجات…';
        const selection = { ...state };
        try {
            const response = await window.authFetch('/dashboard/api/analytics/products/?' + params(selection, data.meta.as_of), { signal: tableController.signal, suppressError: true });
            const result = await responseJSON(response);
            if (id !== tableGeneration || parent !== generation) return;
            committed = selection; data.products = result;
            renderProducts(result); state.page = String(result.page); writeURL(false);
        } catch (error) {
            if (id !== tableGeneration || parent !== generation || error.name === 'AbortError') return;
            failed = true; status(error.message + ' جدول المنتجات المعروض من آخر تحميل ناجح.', true);
            el('product-status').textContent = 'تعذّر تحديث المنتجات.';
        } finally {
            if (id === tableGeneration && parent === generation) { tableBusy = false; controls(); }
        }
    }
    function table(target, headers, rows) {
        const t = node('table', null, 'analytics-table'), head = node('thead'), tr = node('tr');
        headers.forEach(h => { const th = node('th', h); th.scope = 'col'; tr.append(th); });
        head.append(tr); t.append(head);
        const body = node('tbody');
        rows.forEach(row => { const r = node('tr'); row.forEach(value => r.append(node('td', value))); body.append(r); });
        if (!rows.length) { const td = node('td', 'لا توجد بيانات في هذه الفترة.'); td.colSpan = headers.length; const r = node('tr'); r.append(td); body.append(r); }
        t.append(body); el(target).replaceChildren(t);
    }
    function draw(id, type, chartLabels, datasets, options = {}) {
        charts.get(id)?.destroy(); charts.delete(id);
        const canvas = el(id);
        const hasValues = datasets.some(ds => ds.data.some(value => Number(value) !== 0));
        canvas.hidden = !window.Chart || !hasValues;
        if (canvas.hidden) return;
        const style = getComputedStyle(root), color = name => style.getPropertyValue(name).trim();
        const doughnut = type === 'doughnut', horizontal = options.indexAxis === 'y';
        const palette = ['--chart-bronze', '--chart-slate', '--success', '--chart-plum', '--muted', '--warning', '--danger'].map(color);
        const chart = new Chart(canvas, {
            type,
            data: { labels: chartLabels, datasets: datasets.map((ds, i) => ({ borderColor: doughnut ? color('--paper') : palette[i], backgroundColor: doughnut ? palette : palette[i], borderWidth: 2, pointRadius: 2, pointHoverRadius: 5, tension: .25, cubicInterpolationMode: 'monotone', maxBarThickness: 32, borderRadius: type === 'bar' ? 4 : 0, ...ds })) },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: !matchMedia('(prefers-reduced-motion: reduce)').matches,
                color: color('--muted'), font: { family: style.fontFamily },
                indexAxis: horizontal ? 'y' : 'x',
                plugins: {
                    legend: { display: !horizontal, position: 'bottom', rtl: true, labels: { usePointStyle: true, font: { family: style.fontFamily }, color: color('--muted'), padding: 16 } },
                    tooltip: { rtl: true, titleFont: { family: style.fontFamily }, bodyFont: { family: style.fontFamily }, callbacks: {
                        label: context => `${context.dataset.label || context.label}: ${options.money ? currency(context.raw) : count(context.raw)}`,
                        ...(options.trends ? { title: contexts => { const c = contexts[0], row = data.trends[c.dataIndex], previous = c.datasetIndex === 1; return range(row[previous ? 'previous_start' : 'start'], row[previous ? 'previous_end' : 'end']); } } : options.fullLabels ? { title: contexts => options.fullLabels[contexts[0].dataIndex] } : {}),
                    } },
                },
                ...(doughnut ? { cutout: '72%' } : { scales: {
                    x: { beginAtZero: horizontal, grid: { display: horizontal, color: color('--line') }, ticks: { color: color('--muted'), maxRotation: 0, autoSkip: true, maxTicksLimit: 6, ...(horizontal ? { precision: options.money ? 2 : 0, callback: count } : {}) } },
                    y: { beginAtZero: true, grid: { display: !horizontal, color: color('--line') }, ticks: { color: color('--muted'), ...(!horizontal ? { precision: options.money ? 2 : 0, callback: count } : {}) } },
                } }),
            },
        });
        charts.set(id, chart);
    }
    function render(result) {
        const m = result.meta;
        el('period').textContent = `${range(m.start, m.end)} · مقارنة بـ ${range(m.previous_start, m.previous_end)} · ${m.timezone}${m.partial ? ' · فترة جارية حتى وقت التحديث' : ''}`;
        el('updated').textContent = 'آخر تحديث: ' + new Intl.DateTimeFormat('ar-EG', { dateStyle: 'medium', timeStyle: 'short', timeZone: m.timezone }).format(new Date(m.as_of));
        el('start').max = el('end').max = m.as_of.slice(0, 10);
        if (state.preset !== 'custom') { el('start').value = m.start; el('end').value = m.end; }
        const grid = document.getElementById('analytics-kpis-grid'); grid.replaceChildren();
        for (const [key, label] of Object.entries(labels)) {
            const current = result.kpis[key], previous = result.previous[key], delta = result.changes[key];
            const card = node('article', null, 'kpi-card'); card.dataset.metric = key;
            card.append(node('div', label, 'kpi-label'), node('div', metric(key, current), 'kpi-value'));
            const available = delta != null, direction = !available || Number(delta) === 0 ? 'flat' : Number(delta) > 0 ? 'up' : 'down';
            const text = available ? `${Number(delta) > 0 ? '↑' : Number(delta) < 0 ? '↓' : '↔'} ${count(Math.abs(Number(delta)))}٪ عن الفترة السابقة` : current == null ? 'لا توجد قاعدة كافية للحساب' : Number(current) === 0 && Number(previous) === 0 ? 'لا نشاط في الفترتين' : 'لا نسبة مقارنة لغياب أساس سابق';
            card.append(node('span', text, 'kpi-badge ' + direction), node('p', 'الفترة السابقة: ' + metric(key, previous), 'analytics-kpi-note'));
            grid.append(card);
        }
        const facts = [`إيرادات الفترة ${currency(result.kpis.revenue)} من ${count(result.kpis.qualifying_orders)} طلب مؤكد أو مسلّم.`];
        if (result.changes.revenue != null) facts.push(`الإيرادات ${Number(result.changes.revenue) > 0 ? 'ارتفعت' : Number(result.changes.revenue) < 0 ? 'انخفضت' : 'لم تتغير'}${Number(result.changes.revenue) === 0 ? '.' : ' بنسبة ' + count(Math.abs(Number(result.changes.revenue))) + '٪.'}`);
        if (result.summary.leading_product) facts.push(`الأعلى إيراداً: ${result.summary.leading_product.name}.`);
        if (result.summary.leading_channel) facts.push(`أعلى قناة بإيراد منسوب: ${platforms[result.summary.leading_channel.platform]}.`);
        const unlinked = result.channels.find(r => r.platform === 'unattributed');
        if (unlinked?.orders) facts.push(`${count(unlinked.orders)} طلب غير منسوب لقناة.`);
        el('summary').textContent = facts.join(' ');
        el('empty').hidden = result.kpis.orders !== 0 || result.kpis.conversations !== 0;
        const quality = [];
        if (result.kpis.missing_customer_orders) quality.push(`${count(result.kpis.missing_customer_orders)} طلب بدون معرّف عميل قابل للعد.`);
        if (Number(result.reconciliation.difference)) quality.push(`فرق إجمالي الطلبات عن العناصر المحفوظة: ${currency(result.reconciliation.difference)}. لا نوزّع هذا الفرق على منتجات تقديرياً.`);
        if (result.reconciliation.orders_without_items) quality.push(`${count(result.reconciliation.orders_without_items)} طلب مؤهل بدون عناصر محفوظة.`);
        el('quality').textContent = quality.join(' ');
        if (quality.length) el('summary').append(node('p', quality.join(' ')));
        for (const key of ['revenue', 'orders']) {
            const fmt = key === 'revenue' ? currency : count;
            table(key + '-table', ['الفترة', 'الفترة المقارنة', labels[key], 'السابق'], result.trends.map(r => [range(r.start, r.end), range(r.previous_start, r.previous_end), fmt(r[key]), fmt(r['previous_' + key])]));
            el(key + '-summary').textContent = `إجمالي الفترة: ${fmt(result.kpis[key])} · السابقة: ${fmt(result.previous[key])}`;
            draw(key, 'line', result.trends.map(r => date(r.start)), [{ label: 'الفترة الحالية', data: result.trends.map(r => Number(r[key])) }, { label: 'الفترة السابقة', borderDash: [6, 5], data: result.trends.map(r => Number(r['previous_' + key])) }], { money: key === 'revenue', trends: true });
        }
        const statusRows = result.statuses.filter(r => r.orders > 0);
        table('statuses-table', ['الحالة', 'الطلبات'], result.statuses.map(r => [statuses[r.status], count(r.orders)]));
        el('statuses-summary').textContent = statusRows.length ? statusRows.map(r => `${statuses[r.status]}: ${count(r.orders)}`).join(' · ') : 'لا توجد طلبات في هذه الفترة.';
        const chartStyle = getComputedStyle(root), c = token => chartStyle.getPropertyValue(token).trim();
        const statusColors = { pending: c('--chart-bronze'), confirmed: c('--chart-slate'), delivered: c('--success'), cancelled: c('--danger'), unknown: c('--muted') };
        const platformColors = Object.fromEntries(Object.keys(platforms).map((key, i) => [key, ['--chart-bronze', '--success', '--chart-slate', '--chart-plum', '--warning', '--muted', '--danger'][i]]));
        draw('statuses', 'doughnut', statusRows.map(r => statuses[r.status]), [{ label: 'الطلبات', data: statusRows.map(r => r.orders), backgroundColor: statusRows.map(r => statusColors[r.status]) }]);
        const conversationRows = result.channels.filter(r => r.conversations > 0);
        table('platforms-table', ['المنصة', 'المحادثات'], conversationRows.map(r => [platforms[r.platform], count(r.conversations)]));
        el('platforms-summary').textContent = conversationRows.length ? conversationRows.map(r => `${platforms[r.platform]}: ${count(r.conversations)}`).join(' · ') : 'لا توجد محادثات جديدة في هذه الفترة.';
        draw('platforms', 'doughnut', conversationRows.map(r => platforms[r.platform]), [{ label: 'المحادثات', data: conversationRows.map(r => r.conversations), backgroundColor: conversationRows.map(r => c(platformColors[r.platform])) }]);
        for (const [id, rows, key, title] of [['units', result.top_units, 'units', 'الوحدات'], ['product-revenue', result.top_revenue, 'revenue', 'الإيرادات']]) {
            const fmt = key === 'revenue' ? currency : count;
            table(id + '-table', ['المنتج', title], rows.map(r => [r.name, fmt(r[key])]));
            el(id + '-summary').textContent = rows.length ? `${rows[0].name}: ${fmt(rows[0][key])}` : 'لا توجد منتجات مباعة في الطلبات المؤهلة.';
            draw(id, 'bar', rows.map(r => r.name.length > 24 ? r.name.slice(0, 24) + '…' : r.name), [{ label: title, data: rows.map(r => Number(r[key])) }], { indexAxis: 'y', money: key === 'revenue', fullLabels: rows.map(r => r.name) });
        }
        table('channels-table', ['القناة', 'محادثات جديدة', 'كل الطلبات', 'إيرادات مؤهلة'], result.channels.filter(r => r.orders || r.conversations).map(r => [platforms[r.platform], count(r.conversations), count(r.orders), currency(r.revenue)]));
        renderProducts(result.products);
    }
    function renderProducts(products) {
        const body = el('products-body'); body.replaceChildren();
        products.rows.forEach(row => { const tr = node('tr'); [row.name, count(row.units), count(row.orders), currency(row.revenue)].forEach(value => tr.append(node('td', value))); body.append(tr); });
        if (!products.rows.length) { const tr = node('tr'), td = node('td', state.search ? 'لا توجد منتجات تطابق البحث. جرّب اسماً آخر.' : 'لا توجد منتجات مباعة في هذه الفترة.'); td.colSpan = 4; tr.append(td); body.append(tr); }
        el('page').textContent = `صفحة ${count(products.page)} من ${count(products.pages)} · ${count(products.total)} منتج`;
        el('previous').disabled = products.page <= 1; el('next').disabled = products.page >= products.pages;
        el('product-status').textContent = '';
        root.querySelectorAll('[data-analytics-sort]').forEach(button => {
            const selected = state.sort.replace('-', '') === button.dataset.analyticsSort;
            button.parentElement.setAttribute('aria-sort', selected ? state.sort.startsWith('-') ? 'descending' : 'ascending' : 'none');
            button.querySelector('span').textContent = selected ? state.sort.startsWith('-') ? '↓' : '↑' : '↕';
        });
    }
    async function exportCSV() {
        if (el('export').disabled) return;
        const selected = { ...committed, report: state.report }, parent = generation, tableId = tableGeneration;
        exportController?.abort(); exportController = new AbortController(); exporting = true; controls();
        try {
            const response = await window.authFetch('/dashboard/api/analytics/export/?' + params(selected, data.meta.as_of), { signal: exportController.signal, suppressError: true });
            if (!response?.ok) { await responseJSON(response); return; }
            const blob = await response.blob();
            if (parent !== generation || tableId !== tableGeneration) return;
            const url = URL.createObjectURL(blob), a = node('a');
            a.href = url; a.download = `analytics-${selected.report}-${data.meta.start}-${data.meta.end}.csv`;
            document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
            if (error.name !== 'AbortError' && parent === generation && tableId === tableGeneration) status(error.message, true);
        } finally { exporting = false; controls(); }
    }
    el('preset').addEventListener('change', () => {
        state.preset = el('preset').value; state.page = '1';
        if (state.preset === 'custom') { ++generation; ++tableGeneration; mainController?.abort(); tableController?.abort(); busy = false; tableBusy = false; state.start = el('start').value; state.end = el('end').value; draft = true; syncControls(); controls(); status('اختر تاريخ البداية والنهاية ثم اضغط تطبيق الفترة.'); }
        else { syncControls(); writeURL(); load(); }
    });
    el('filters').addEventListener('submit', event => { event.preventDefault(); state.start = el('start').value; state.end = el('end').value; state.page = '1'; writeURL(); load(); });
    ['start', 'end'].forEach(key => el(key).addEventListener('input', () => { draft = true; controls(); }));
    el('aggregation').addEventListener('change', () => { state.aggregation = el('aggregation').value; state.page = '1'; writeURL(); load(); });
    el('report').addEventListener('change', () => { state.report = el('report').value; writeURL(); });
    el('refresh').addEventListener('click', load); el('retry').addEventListener('click', load); el('export').addEventListener('click', exportCSV);
    el('search').addEventListener('input', () => {
        state.search = el('search').value; state.page = '1';
        if (busy) { ++generation; mainController?.abort(); busy = false; }
        // Invalidate immediately, including the debounce interval.
        ++tableGeneration; tableController?.abort(); tableBusy = true; controls();
        clearTimeout(searchTimer); searchTimer = setTimeout(() => { writeURL(); loadProducts(); }, 300);
    });
    root.querySelectorAll('[data-analytics-sort]').forEach(button => button.addEventListener('click', () => {
        const key = button.dataset.analyticsSort; state.sort = state.sort === key ? '-' + key : key; state.page = '1'; writeURL(); loadProducts();
    }));
    el('previous').addEventListener('click', () => { state.page = String(Math.max(1, Number(state.page) - 1)); writeURL(); loadProducts(); });
    el('next').addEventListener('click', () => { state.page = String(Number(state.page) + 1); writeURL(); loadProducts(); });
    window.StoreAnalytics = {
        show() { if (!busy || restoring) load(); requestAnimationFrame(() => charts.forEach(chart => chart.resize())); },
        sectionChanged(name) { if (!restoring) writeURL(true, name); },
    };
    window.addEventListener('popstate', () => {
        ++generation; ++tableGeneration; mainController?.abort(); tableController?.abort(); exportController?.abort();
        busy = false; tableBusy = false;
        state = readURL(); syncControls(); restoring = true;
        const section = new URLSearchParams(location.search).get('section') || 'overview';
        window.showSection(document.getElementById('section-' + section) ? section : 'overview'); restoring = false;
    });
    syncControls();
    const section = new URLSearchParams(location.search).get('section');
    if (section && document.getElementById('section-' + section)) { restoring = true; window.showSection(section); restoring = false; }
})();
