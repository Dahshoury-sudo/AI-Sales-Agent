// Synthetic data ONLY for the template preview and browser checks.
const now = '2026-10-22T12:00:00+00:00';
const iso = d => d.toISOString().slice(0, 10);
const add = (d, n) => new Date(new Date(d + 'T00:00:00Z').getTime() + n * 86400000);
function fixture(url, empty = false) {
    const p = new URL(url).searchParams, preset = p.get('preset') || '30d';
    const end = preset === 'custom' ? p.get('end') : '2026-10-22';
    const start = preset === 'custom' ? p.get('start') : preset === 'month' ? '2026-10-01' : iso(add(end, preset === 'today' ? 0 : preset === '7d' ? -6 : -29));
    const days = Math.round((add(end, 0) - add(start, 0)) / 86400000) + 1;
    const meta = { start, end, previous_start: iso(add(start, -days)), previous_end: iso(add(end, -days)), as_of: now, partial: end === '2026-10-22', timezone: 'UTC', preset, aggregation: p.get('aggregation') || 'daily', search: p.get('search') || '', sort: p.get('sort') || '-revenue' };
    const value = empty ? 0 : 1;
    const products = Array.from({ length: empty ? 0 : 24 }, (_, i) => ({ product_key: i + 1, name: i === 0 ? 'Oud Wood' : `عطر ${String(i + 1).padStart(2, '0')}`, units: 25 - i, orders: 25 - i, revenue: String((25 - i) * 100) }));
    let filtered = products.filter(r => r.name.toLowerCase().includes(meta.search.toLowerCase()));
    const key = meta.sort.replace('-', ''), sign = meta.sort.startsWith('-') ? -1 : 1;
    filtered.sort((a, b) => sign * (key === 'name' ? a.name.localeCompare(b.name) : Number(a[key]) - Number(b[key])));
    const page = Math.min(Number(p.get('page') || 1), Math.max(1, Math.ceil(filtered.length / 20)));
    const productPage = { rows: filtered.slice((page - 1) * 20, page * 20), total: filtered.length, page, pages: Math.max(1, Math.ceil(filtered.length / 20)), page_size: 20 };
    const trends = Array.from({ length: days }, (_, i) => ({ start: iso(add(start, i)), end: iso(add(start, i)), previous_start: iso(add(start, i - days)), previous_end: iso(add(start, i - days)), revenue: String(100 * value), previous_revenue: String(80 * value), orders: value, previous_orders: value }));
    const kpis = { revenue: String(days * 100 * value), orders: days * value, qualifying_orders: days * value, aov: value ? '100.00' : null, customers: days * value, conversations: days * 2 * value, converted_conversations: days * value, conversion: value ? '50.0' : null, missing_customer_orders: 0 };
    const previous = { ...kpis, revenue: String(days * 80 * value), aov: value ? '80.00' : null };
    const channels = [{ platform: 'web', conversations: days * value, orders: days * value, revenue: String(days * 100 * value) }, { platform: 'whatsapp', conversations: days * value, orders: 0, revenue: '0' }, { platform: 'unattributed', conversations: 0, orders: 0, revenue: '0' }];
    return { meta, kpis, previous, changes: { revenue: value ? '25.0' : null, aov: value ? '25.0' : null, orders: value ? '0.0' : null, conversations: value ? '0.0' : null, customers: value ? '0.0' : null, conversion: value ? '0.0' : null }, trends, channels, statuses: [{ status: 'confirmed', orders: days * value }], top_units: products.slice(0, 5), top_revenue: products.slice(0, 5), products: productPage, reconciliation: { difference: '0', orders_without_items: 0 }, summary: { leading_product: products[0] || null, leading_channel: value ? channels[0] : null } };
}
module.exports = { fixture };
