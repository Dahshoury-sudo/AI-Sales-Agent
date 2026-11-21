const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {intercept,base,ready,apiCalls,setEmpty,profile,recentPosts}=require('./check_layout.cjs');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const passed=[];
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  await context.route('**/*',intercept);
  await context.addInitScript(()=>{localStorage.setItem('access_token','preview');localStorage.setItem('refresh_token','preview-refresh');localStorage.setItem('perfume_api_key','preview');});
  const page=await context.newPage();page.on('dialog',d=>d.accept());
  await page.goto(base+'/dashboard/',{waitUntil:'networkidle'});await ready(page);
  async function section(name){await page.evaluate(s=>showSection(s),name);await ready(page);}
  async function posted(since,match){await page.waitForTimeout(200);const call=apiCalls.slice(since).find(c=>c.path.includes(match));assert.ok(call,'Missing request '+match);return call;}

  await page.locator('.platform-row').first().waitFor();
  assert.deepEqual(await page.locator('.platform-share').allTextContents(), ['50%', '25%', '18.8%', '6.3%']);
  for(const [index,destination] of [[0,'products'],[1,'bulk_import'],[2,'analytics']]){
   await section('overview');await page.locator('.quick-action').nth(index).click();
   assert.equal(await page.locator('.section.active').getAttribute('id'),'section-'+destination);
   assert.equal(await page.locator('#workspace-section-title').innerText(),await page.locator('#nav-'+destination).innerText());
  }
  await page.locator('.notification-shortcut').click();assert.equal(await page.locator('.section.active').getAttribute('id'),'section-notifications');
  const overviewRoute='**/dashboard/api/overview/';
  await page.route(overviewRoute,route=>route.fulfill({contentType:'application/json',body:JSON.stringify({total_conversations:0,total_orders:0,revenue:'0',pending_orders:0,pending_handoffs:0,platform_breakdown:{web:0}})}));
  await section('overview');await page.waitForFunction(()=>document.querySelector('#stat-revenue').textContent==='0');
  assert.match(await page.locator('#platform-breakdown').innerText(),/لا توجد محادثات/);
  assert.equal(await page.locator('.platform-row').count(),0);
  await page.unroute(overviewRoute);
  await page.route(overviewRoute,route=>route.fulfill({status:503,contentType:'text/html',body:'<!doctype html><title>Unavailable</title>'}));
  await section('overview');await page.locator('#overview-message.is-error').waitFor();
  assert.equal(await page.locator('#stat-revenue').innerText(),'—');
  await page.unroute(overviewRoute);await page.locator('#overview-message button').click();
  await page.locator('#overview-message').waitFor({state:'hidden'});assert.equal(await page.locator('#stat-revenue').innerText(),'48,500');
  passed.push('Overview percentages, zero-data state, failure/retry, quick actions and header navigation');

  const unusualPlatform='<img src=x onerror=alert(1)>';
  await page.route(overviewRoute,route=>route.fulfill({contentType:'application/json',body:JSON.stringify({total_conversations:999999999,total_orders:99999999,revenue:'999999999999.99',pending_orders:9999999,pending_handoffs:999999,platform_breakdown:{[unusualPlatform]:5}})}));
  await page.route('**/dashboard/api/profile/',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({...profile,full_name:'محمد أحمد صاحب متجر العطور والمنتجات الطبيعية',store:{...profile.store,name:'متجر العطور والمنتجات الطبيعية لجميع المناسبات'}})}));
  await page.setViewportSize({width:375,height:1000});await section('overview');
  await page.waitForFunction(()=>document.querySelector('.platform-name')?.textContent.startsWith('<img'));
  assert.equal(await page.locator('.platform-name').innerText(),unusualPlatform);
  assert.equal(await page.locator('#platform-breakdown img').count(),0);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Large values or Arabic names overflow on mobile');
  await page.unroute(overviewRoute);await page.unroute('**/dashboard/api/profile/');await page.setViewportSize({width:1440,height:1000});await section('overview');
  passed.push('Large metrics, long Arabic names and safe rendering of unknown platform names');

  await section('products');
  await page.locator('#product-search').fill('Oud');await page.locator('#product-search').press('d');await page.waitForTimeout(500);
  const edit=page.locator('#products-tbody button').first();await edit.click();
  await page.waitForFunction(()=>document.activeElement.id==='edit-product-name');
  assert.equal(await page.locator('#edit-product-modal').getAttribute('role'),'dialog');
  await page.locator('#edit-product-name').fill('Oud Wood Updated');
  let since=apiCalls.length;await page.locator('#edit-product-modal button', {hasText:'حفظ'}).click();
  const productCall=await posted(since,'/products/1/');assert.equal(productCall.method,'PUT');assert.equal(JSON.parse(productCall.data).name,'Oud Wood Updated');
  await edit.click();await page.keyboard.press('Escape');await page.locator('#edit-product-modal').waitFor({state:'hidden'});assert.ok(await edit.evaluate(el=>el===document.activeElement));
  passed.push('Product search, editor, save payload, Escape and focus restoration');

  await section('faq');await page.evaluate(()=>openFaqModal());await ready(page);
  await page.locator('#faq-question').fill('هل يوجد توصيل؟');await page.locator('#faq-answer').fill('نعم، لكل المحافظات.');await page.locator('#faq-keywords').fill('توصيل');
  since=apiCalls.length;await page.locator('#faq-modal button',{hasText:'حفظ'}).click();await posted(since,'/faqs/');
  await section('post_rules');
  assert.match(await page.locator('#post-rules-tbody').innerText(),/مش هيشتغل/);
  assert.equal(await page.locator('#post-rules-tbody tr').filter({hasText:'مش هيشتغل'}).count(),1);
  // Pick a post from the list: its Meta ID is what gets saved, and the caption fills the label.
  await page.evaluate(()=>openPostRuleModal());await ready(page);
  const picked=page.locator('#post-rule-picker button[data-post-id="111_900"]');await picked.waitFor();
  assert.ok(await page.locator('#post-rule-picker button[data-post-id="111_123456789"]').isDisabled(),'Already-blocked post is selectable');
  await picked.click();assert.equal(await picked.getAttribute('aria-pressed'),'true');
  const autoLabel=recentPosts.posts[0].text.slice(0,60);
  assert.equal(await page.locator('#post-rule-label').inputValue(),autoLabel);
  since=apiCalls.length;await page.locator('#post-rule-modal button',{hasText:'حفظ'}).click();
  let ruleCall=await posted(since,'/post-rules/');assert.equal(ruleCall.method,'POST');
  assert.deepEqual(JSON.parse(ruleCall.data),{platform:'facebook',post_id:'111_900',label:autoLabel});
  // Pasting a link clears the picked post and is sent instead.
  await page.evaluate(()=>openPostRuleModal());await ready(page);await picked.click();
  await page.locator('#post-rule-post-id').fill('123456789');await page.locator('#post-rule-label').fill('بوست تجريبي');
  assert.equal(await picked.getAttribute('aria-pressed'),'false');
  since=apiCalls.length;await page.locator('#post-rule-modal button',{hasText:'حفظ'}).click();
  ruleCall=await posted(since,'/post-rules/');assert.equal(JSON.parse(ruleCall.data).post_id,'123456789');
  // Listing failure: the reason shows and pasting is offered.
  await page.route('**/dashboard/api/post-rules/recent/**',route=>route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'لازم تربط الـ Facebook Page ID والـ Access Token في إعدادات الستور الأول.'})}));
  await page.evaluate(()=>openPostRuleModal());
  await page.waitForFunction(()=>document.getElementById('post-rule-picker-status').textContent.includes('إعدادات الستور'));
  assert.match(await page.locator('#post-rule-picker-status').innerText(),/تلصق رابط البوست/);
  assert.equal(await page.locator('#post-rule-picker button').count(),0);
  await page.keyboard.press('Escape');await page.unroute('**/dashboard/api/post-rules/recent/**');
  passed.push('FAQ and post-rule creation: pick from recent posts, paste fallback, listing error, dead-rule warning');

  await section('settings');await page.locator('#setting-system-prompt').fill('تعليمات تجريبية');
  const promotionField=page.getByLabel('العروض والباندلز والخصومات الحالية',{exact:true});
  assert.equal(await promotionField.inputValue(),'بوكس الصيف: 3 عطور بـ3000 جنيه.\nهدية 50 مل مع العرض.');
  const promotionText='عرض الشتاء: عطرين بـ900 جنيه.\n\nهدية 50 مل مع العرض.';
  await promotionField.fill(promotionText);
  since=apiCalls.length;await page.locator('#section-settings button',{hasText:'حفظ الإعدادات'}).first().click();const settingCall=await posted(since,'/settings/');assert.equal(JSON.parse(settingCall.data).system_prompt,'تعليمات تجريبية');
  assert.equal(JSON.parse(settingCall.data).promotions,promotionText);
  assert.equal(JSON.parse(settingCall.data).business_facts,'متاح التوصيل لكل المحافظات.');
  await section('overview');await section('settings');
  assert.equal(await promotionField.inputValue(),promotionText);
  const promotionsCard=page.locator('.settings-panel').filter({has:promotionField});
  assert.equal(await promotionsCard.locator('#setting-website-ordering').count(),1);
  await promotionField.fill('');
  since=apiCalls.length;await promotionsCard.getByRole('button',{name:'حفظ الإعدادات'}).click();
  assert.equal(JSON.parse((await posted(since,'/settings/')).data).promotions,'');
  await section('overview');await section('settings');
  assert.equal(await promotionField.inputValue(),'');
  passed.push('Promotions load, save multiline text independently, reload and clear');
  await section('meta');
  await page.waitForFunction(()=>document.getElementById('meta-webhook-url').value==='https://api.webvitas.com/api/webhooks/meta/');
  await page.locator('#section-meta button',{hasText:'توليد'}).click();
  assert.match(await page.locator('#meta-verify-token').inputValue(),/^wv_[0-9a-f]{36}$/);
  // The check saves what's on screen first, then asks the server to check and fix.
  since=apiCalls.length;await page.locator('#meta-check-button').click();
  await page.locator('#meta-check-results li').nth(3).waitFor();
  const metaCalls=apiCalls.slice(since).map(c=>c.path);
  const saveAt=metaCalls.findIndex(p=>p.endsWith('/settings/')),checkAt=metaCalls.findIndex(p=>p.endsWith('/meta/check/'));
  assert.ok(saveAt>=0&&checkAt>saveAt,'Check must save first: '+metaCalls.join(','));
  assert.match(JSON.parse(apiCalls[since+saveAt].data).meta_verify_token,/^wv_/);
  assert.equal(await page.locator('#meta-ig-id').inputValue(),'17841400000000001');
  assert.equal(await page.locator('#meta-fb-page-id').inputValue(),'1110001');
  assert.match(await page.locator('#meta-check-status').innerText(),/1 حاجة محتاجة/);
  assert.equal(await page.locator('#meta-check-results [aria-label="محتاج يتظبط"]').count(),1);
  assert.equal(await page.locator('#meta-check-results [aria-label="مقدرناش نفحصه"]').count(),1);
  await page.route('**/dashboard/api/meta/check/',route=>route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'حط توكن الصفحة (Page Token) واحفظ الإعدادات الأول.'})}));
  await page.locator('#meta-check-button').click();
  await page.waitForFunction(()=>document.getElementById('meta-check-status').textContent.includes('Page Token'));
  assert.equal(await page.locator('#meta-check-results li').count(),0);
  assert.ok(await page.locator('#meta-check-button').isEnabled());
  await page.unroute('**/dashboard/api/meta/check/');
  since=apiCalls.length;await page.locator('#section-meta button',{hasText:'حفظ'}).last().click();await posted(since,'/settings/');
  since=apiCalls.length;await section('notifications');await posted(since,'/notifications/mark-read/');
  passed.push('Settings, platform connection form (webhook URL, verify token, save-then-check, results, error) and notification read action');

  await section('orders');const orderFrame=await (await page.locator('#iframe-orders').elementHandle()).contentFrame();await ready(orderFrame);
  await orderFrame.locator('.filter-btn').nth(1).click();assert.equal(await orderFrame.locator('#orders-body tr').count(),1);
  await orderFrame.locator('#orders-body tr').first().focus();await page.keyboard.press('Enter');await orderFrame.locator('#order-modal.active').waitFor();
  since=apiCalls.length;await orderFrame.locator('.btn-confirm').click();const statusCall=await posted(since,'/orders/1/status/');assert.equal(JSON.parse(statusCall.data).status,'confirmed');
  await page.keyboard.press('Escape');passed.push('Order filtering, keyboard detail dialog and status action');

  for(const name of ['handoff','chat_history']){
   await section(name);const frame=await(await page.locator('#iframe-'+name).elementHandle()).contentFrame();await ready(frame);
   await frame.locator('.conv-item').first().focus();await page.keyboard.press('Enter');await frame.locator('#chat-area').waitFor();
   await frame.locator('#chat-input').fill('أهلاً، نقدر نساعدك.');since=apiCalls.length;await frame.locator('.btn-send').click();await posted(since,'/conversations/1/reply/');
   if(name==='handoff'){since=apiCalls.length;await frame.locator('#btn-resolve').click();await posted(since,'/conversations/1/resolve/');}
  }
  passed.push('Conversation keyboard selection, replies, and handoff resolution');

  await section('chat_demo');const demo=await(await page.locator('#iframe-chat_demo').elementHandle()).contentFrame();await ready(demo);
  await demo.locator('#chat-input').fill('عطر للشغل');since=apiCalls.length;await demo.locator('#send-btn').click();await posted(since,'/api/chat/');
  await demo.locator('.chat-reset').click();assert.equal(await demo.evaluate(()=>localStorage.getItem('perfume_conv_id')),null);
  passed.push('Test-chat send and reset');
  await section('bulk_import');const imp=await(await page.locator('#iframe-bulk_import').elementHandle()).contentFrame();await ready(imp);
  await imp.locator('#fileInput').setInputFiles({name:'products.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from('Mock upload body; server intercepted')});
  since=apiCalls.length;await imp.locator('#uploadBtn').click();await posted(since,'/import/upload/');
  passed.push('Excel import selection and upload');

  setEmpty(true);for(const name of ['products','faq','post_rules','notifications']){await section(name);assert.match(await page.locator('#section-'+name).innerText(),/لا توجد/);}setEmpty(false);
  passed.push('Empty products, FAQs, post rules and notifications');

  await page.route('**/dashboard/api/products/**', route => route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Temporarily unavailable'})}));
  await section('products');await page.locator('#wv-request-error').waitFor({state:'visible'});
  await page.locator('#wv-request-error button').click();await page.unroute('**/dashboard/api/products/**');
  let refreshAttempts=0;
  await page.route('**/dashboard/api/faqs/',route=>{
   refreshAttempts++;
   if(refreshAttempts===1)return route.fulfill({status:401,contentType:'application/json',body:'{}'});
   return intercept(route);
  });
  since=apiCalls.length;await section('faq');await posted(since,'/token/refresh/');assert.equal(refreshAttempts,2);
  await page.unroute('**/dashboard/api/faqs/');
  passed.push('Visible request errors and expired-token refresh');

  await page.setViewportSize({width:375,height:900});await section('overview');
  const toggle=page.locator('.mobile-toggle');await toggle.focus();await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('.mobile-toggle').getAttribute('aria-expanded')==='true');
  assert.equal(await page.locator('#main-content').evaluate(el=>el.inert),true);
  await page.keyboard.press('Escape');assert.ok(await toggle.evaluate(el=>el===document.activeElement));
  await toggle.click();await page.locator('#nav-handoff').click();
  assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  const handoff=await(await page.locator('#iframe-handoff').elementHandle()).contentFrame();
  if(await handoff.locator('.conversation-back').isVisible())await handoff.locator('.conversation-back').click();
  await handoff.locator('.conv-item').first().click();assert.equal(await handoff.locator('.sidebar').isVisible(),false);
  await handoff.locator('.conversation-back').click();assert.equal(await handoff.locator('.sidebar').isVisible(),true);
  passed.push('Mobile drawer, inert background, Escape, navigation, and chat back control');

  const auth=await browser.newContext({viewport:{width:375,height:900},reducedMotion:'reduce'});await auth.route('**/*',intercept);const ap=await auth.newPage();
  await ap.goto(base+'/dashboard/login/',{waitUntil:'networkidle'});
  await ap.route('**/dashboard/api/login/',route=>route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'بيانات تسجيل الدخول غير صحيحة'})}));
  await ap.locator('#email').fill('owner@example.test');await ap.locator('#password').fill('test-password');await ap.locator('#btn-login').click();await ap.locator('#error-msg').waitFor({state:'visible'});assert.equal(await ap.locator('#btn-login').isDisabled(),false);
  await ap.unroute('**/dashboard/api/login/');await ap.locator('#btn-login').click();await ap.waitForURL('**/dashboard/');
  await ap.evaluate(()=>localStorage.clear());await ap.goto(base+'/dashboard/register/',{waitUntil:'networkidle'});
  await ap.locator('#full_name').fill('محمد');await ap.locator('#store_name').fill('متجر التجربة');await ap.locator('#email').fill('owner@example.test');await ap.locator('#password').fill('test-password');await ap.locator('#register-form button').click();await ap.waitForURL('**/dashboard/');
  await ap.evaluate(()=>localStorage.clear());await ap.goto(base+'/dashboard/forgot-password/',{waitUntil:'networkidle'});await ap.locator('#email').fill('owner@example.test');since=apiCalls.length;await ap.locator('#forgot-form button').click();await posted(since,'/forgot-password/');
  await ap.goto(base+'/dashboard/reset-password/preview/token/',{waitUntil:'networkidle'});await ap.locator('#password').fill('test-password');await ap.locator('#password2').fill('different-password');await ap.locator('#reset-form button').click();await ap.locator('#error-msg').waitFor({state:'visible'});await ap.locator('#password2').fill('test-password');since=apiCalls.length;await ap.locator('#reset-form button').click();await posted(since,'/reset-password/');await ap.waitForURL('**/dashboard/login/');
  passed.push('Login error/success, registration, password recovery, mismatch and reset');
  fs.writeFileSync(path.join(require('./check_layout.cjs').out,'workflows-report.json'),JSON.stringify({passed},null,2));console.log(JSON.stringify({passed},null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
