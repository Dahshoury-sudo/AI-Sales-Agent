/* Offline behavior checks for the shipped chat scripts; no browser/network required.
 * Run: node scripts/verify_chat_clients.js
 * Covers retry identity, overlapping submissions, public polling and chat reset.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');

const root = path.resolve(__dirname, '..');

function environment() {
  const nodes = new Map(), storage = new Map([['perfume_api_key', 'test-key']]);
  const intervals = [];
  function node() {
    return {
      children: [], events: {}, style: {}, value: '', textContent: '',
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener(name, handler) { this.events[name] = handler; },
      appendChild(child) {
        this.children = this.children.filter(item => item !== child);
        this.children.push(child);
      },
      set innerHTML(value) { this.children = []; this.html = value; },
      focus() {},
      attachShadow() { return { appendChild() {}, getElementById: get }; },
    };
  }
  function get(id) {
    if (!nodes.has(id)) nodes.set(id, node());
    return nodes.get(id);
  }
  const document = {
    body: node(), hidden: false,
    currentScript: { src: 'https://example.test/static/widget/perfume-chat.js',
      getAttribute: name => name === 'data-api-key' ? 'test-key' : null },
    createElement: node, getElementById: get, addEventListener() {},
  };
  const context = vm.createContext({
    document, window: { innerWidth: 1200 }, console,
    localStorage: { getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    crypto: { randomUUID }, setInterval: handler => intervals.push(handler),
    setTimeout() {}, requestAnimationFrame: handler => handler(),
    fetch: () => { throw new Error('Unexpected network request'); },
  });
  return { context, get, intervals, storage };
}

async function check(filename, widget, storageKey) {
  let source = fs.readFileSync(path.join(root, filename), 'utf8');
  if (!widget) source = [...source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]).join('\n');
  const env = environment();
  new vm.Script(source, { filename }).runInContext(env.context);
  const input = env.get(widget ? 'pfxInput' : 'chat-input');
  const messages = env.get(widget ? 'pfxMessages' : 'chat-window');
  const send = widget ? env.get('pfxSend').events.click : () => vm.runInContext('sendMessage()', env.context);
  const reset = widget ? env.get('pfxNewChat').events.click : () => vm.runInContext('resetChat()', env.context);
  const poll = env.intervals[0];
  if (widget) env.get('pfxBubble').events.click();

  const posts = [];
  env.context.fetch = async (_, options) => {
    posts.push(JSON.parse(options.body));
    throw new Error('Lost response');
  };
  input.value = 'hello';
  await send();
  assert.equal(input.value, 'hello', 'Failed request can be retried');
  assert.ok(env.storage.get(storageKey + ':pending'));

  let acknowledge;
  env.context.fetch = (_, options) => {
    posts.push(JSON.parse(options.body));
    return new Promise(resolve => { acknowledge = resolve; });
  };
  const retry = send();
  input.value = 'overlapping';
  await send();
  assert.equal(posts.length, 2, 'Second click while submitting is ignored');
  assert.equal(posts[0].client_message_id, posts[1].client_message_id, 'Retry reuses the first ID');
  acknowledge({ ok: true, json: async () => ({ conversation_id: 'signed-token',
    user_message_id: 1, message_id: 2, reply: 'assistant reply' }) });
  await retry;
  assert.equal(env.storage.get(storageKey + ':pending'), undefined);
  assert.equal(messages.children.filter(m => m.textContent === 'hello').length, 1);

  const urls = [];
  env.context.fetch = async url => {
    urls.push(url);
    return { ok: true, json: async () => ({ after_id: 3, needs_human: true, messages: [
      { id: 1, role: 'user', content: 'hello' },
      { id: 2, role: 'assistant', content: 'assistant reply' },
      { id: 3, role: 'agent', content: 'staff reply', attachment_url: 'https://example.test/item.png' },
    ] }) };
  };
  await poll();
  await poll();
  assert.ok(urls[1].includes('after_id=3'));
  for (const text of ['hello', 'assistant reply', 'staff reply']) {
    assert.equal(messages.children.filter(m => m.textContent === text).length, 1, 'Server echo renders once');
  }
  assert.equal(messages.children.find(m => m.textContent === 'staff reply').children[0].src, 'https://example.test/item.png');

  let finishPoll;
  env.context.fetch = () => new Promise(resolve => { finishPoll = resolve; });
  const stale = poll();
  reset();
  finishPoll({ ok: true, json: async () => ({ after_id: 4, messages: [{ id: 4, role: 'agent', content: 'stale reply' }] }) });
  await stale;
  assert.equal(messages.children.filter(m => m.textContent === 'stale reply').length, 0);
  assert.equal(env.storage.get(storageKey), undefined);
  console.log('PASS ' + filename);
}

(async () => {
  await check('products/static/widget/perfume-chat.js', true, 'pfx_widget_conv_id');
  await check('products/templates/products/chat.html', false, 'perfume_conv_id');
  await check('products/templates/products/public_chat.html', false, 'perfume_demo_conv_id');
})().catch(error => { console.error(error); process.exitCode = 1; });
