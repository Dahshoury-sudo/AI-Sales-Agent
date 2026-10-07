const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = 'http://127.0.0.1:8766';
const out = process.env.DASHBOARD_UI_ARTIFACTS || path.join(require('node:os').tmpdir(), 'webvitas-dashboard-review'); fs.mkdirSync(out, { recursive: true });
const profile = {full_name:'محمد أحمد',email:'owner@example.test',store:{name:'متجر عطور الأصيل',api_key:'preview-only-key'}};
const products = [{id:1,name:'Oud Wood',brand:'Tom Ford',gender:'unisex',is_active:true,description:'عطر خشبي هادئ للاستخدام اليومي',top_notes:'هيل',middle_notes:'عود',base_notes:'مسك',season:'شتاء',occasion:'العمل',longevity:'٨ ساعات',projection:'متوسط',concentration:'EDP',variants:[{id:1,volume:100,price:'850.00',bottle_type:'normal',stock:20}]},{id:2,name:'Bleu de Chanel',brand:'Chanel',gender:'male',is_active:true,description:'عطر منعش',variants:[]}];
const orders = ['pending','confirmed','delivered','cancelled'].map((status,i)=>({id:i+1,status,customer_name:['أحمد محمد','سارة علي','يوسف حسن','نور أحمد'][i],customer_phone:'01000000000',secondary_phone:'',shipping_address:'مدينة نصر، القاهرة',created_at:'2026-10-07T10:00:00Z',total_price:'850.00',bot_notes:'يفضل التوصيل بعد الظهر',items:[{product_name:'Oud Wood',volume:100,quantity:1,unit_price:'850',line_total:'850'}]}));
const conversations=[{id:1,platform:'whatsapp',sender_id:'201000000000',last_message:'عايز عطر مناسب للشغل',created_at:'2026-10-07T10:00:00Z',is_handoff:true,status:'handoff',handoff_reason:'يريد التحدث مع الفريق',message_count:3},{id:2,platform:'instagram',sender_id:'sara',last_message:'الشحن بكام للقاهرة؟',created_at:'2026-10-07T09:00:00Z',message_count:2}];
const messages=[{id:1,role:'user',content:'عايز عطر خشبي هادي ينفع للشغل',created_at:'2026-10-07T10:00:00Z'},{id:2,role:'assistant',content:'أكيد! أرشّحلك عطر عود، خشب أرز ولمسة مسك. تحب أي حجم؟',created_at:'2026-10-07T10:01:00Z'},{id:3,role:'agent',content:'أهلاً أحمد، أنا معاك لو محتاج مساعدة في الطلب.',created_at:'2026-10-07T10:02:00Z'}];
const analytics={kpis:{total_revenue:48500,total_orders:82,total_conversations:320,unique_customers:213,conversion_rate:25.6,avg_order_value:591.46},period_comparisons:[0,1,2].map(()=>({current:{revenue:8500,orders:12,conversations:42},previous:{revenue:6200,orders:9,conversations:34},change:{revenue:37,orders:33,conversations:24}})),order_status_breakdown:{pending:12,confirmed:18,delivered:48,cancelled:4},platform_breakdown:{whatsapp:160,messenger:80,instagram:60,web:20},top_products:[{name:'Oud Wood',qty:24},{name:'Bleu de Chanel',qty:18}]};
const settings={system_prompt:'تحدث بأسلوب طبيعي، ورشّح من منتجات المتجر.',payment_instructions:'الدفع عند الاستلام.',business_facts:'متاح التوصيل لكل المحافظات.',bottle_image_url:'',comment_reply_messages:['أهلاً بيك، بعتنالك التفاصيل.']};
const notifications={unread_count:1,notifications:[{id:1,type:'new_order',title:'طلب جديد من أحمد',message:'طلب جديد لعطر Oud Wood بحجم ١٠٠ مل.',is_read:false,created_at:'2026-10-07T10:00:00Z'}]};
const faqs={faqs:[{id:1,question:'الشحن بكام؟',answer:'تكلفة الشحن حسب المحافظة.',keywords:'شحن,توصيل',priority:1,is_active:true}]};
const rules=[{id:1,platform:'facebook',post_id:'123456789',label:'إعلان الصيف',is_active:true}];
const apiCalls=[];
let empty=false;
async function intercept(route) {
 const req=route.request(),url=new URL(req.url()),p=url.pathname;
 if(url.origin!==base) {
  const staticHosts=['fonts.googleapis.com','fonts.gstatic.com','cdnjs.cloudflare.com','cdn.jsdelivr.net'];
  return staticHosts.includes(url.hostname) ? route.continue() : route.abort();
 }
 if(req.isNavigationRequest() || p.startsWith('/static/')) return route.continue();
 let data;
 if(req.method()!=='GET') {
  apiCalls.push({path:p,method:req.method(),data:req.postData()});
  data=p.includes('token/refresh')?{access:'preview-token-new'}:p.includes('/chat/')?{reply:'أهلاً بيك!',conversation_id:'preview-conversation'}:p.includes('/import/')?{created:2,updated:0,errors:[],warnings:[]}:{success:true,...profile,access:'preview-token',refresh:'preview-refresh'};
 } else if(p.endsWith('/profile/')) data=profile;
 else if(p.endsWith('/overview/')) data={total_conversations:320,total_orders:82,revenue:48500,pending_orders:12,pending_handoffs:3,platform_breakdown:analytics.platform_breakdown};
 else if(p.endsWith('/analytics/')) data=analytics;
 else if(p.endsWith('/products/')) data={products:empty?[]:products,total:2,page:1,total_pages:1};
 else if(/\/products\/\d+\/$/.test(p)) data=products[0];
 else if(p.endsWith('/settings/')) data=settings;
 else if(p.endsWith('/notifications/')) data=empty?{notifications:[],unread_count:0}:notifications;
 else if(p.endsWith('/faqs/')) data=empty?{faqs:[]}:faqs;
 else if(p.endsWith('/post-rules/')) data=empty?[]:rules;
 else if(p.endsWith('/orders/dashboard/')) data={store_name:profile.store.name,orders:empty?[]:orders};
 else if(p.endsWith('/conversations/')) data={conversations:empty?[]:conversations};
 else if(p.endsWith('/messages/')) data={messages,after_id:3};
 else { console.log('UNMOCKED',p); return route.fulfill({status:404,body:'Unmocked local request'}); }
 return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
}
async function ready(page){await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(120);}
async function overflow(frame){return frame.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));}

module.exports = { intercept, base, out, ready, overflow, profile, products, orders, apiCalls, setEmpty: value => { empty = value; } };
if (require.main === module) (async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  await context.route('**/*',intercept);
  await context.addInitScript(()=>{localStorage.setItem('access_token','preview-token');localStorage.setItem('refresh_token','preview-refresh');localStorage.setItem('perfume_api_key','preview-only-key');});
  const page=await context.newPage();
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.goto(base+'/dashboard/',{waitUntil:'networkidle'});await ready(page);
  const sections=['overview','analytics','products','orders','handoff','chat_demo','chat_history','faq','post_rules','bulk_import','settings','meta','notifications'];
  const layouts=[];
  for(const width of [1440,1024,768,375]){
   await page.setViewportSize({width,height:1000});
   for(const section of sections){
    await page.evaluate(s=>showSection(s),section);
    await ready(page);
    const frameEl=page.locator('#iframe-'+section);
    if(await frameEl.count()){
     const frame=await (await frameEl.elementHandle()).contentFrame();
     await frame.waitForLoadState('domcontentloaded');
     if(section==='handoff'||section==='chat_history'){
      if(await frame.locator('.conversation-back').isVisible()) await frame.locator('.conversation-back').click();
      await frame.locator('.conv-item').first().waitFor();await frame.locator('.conv-item').first().click();
     }
     await ready(frame);
    }
    const activeFrames = [page.mainFrame()];
    if(await frameEl.count()) activeFrames.push(await (await frameEl.elementHandle()).contentFrame());
    const checks=await Promise.all(activeFrames.map(overflow));
    const bad=checks.filter(x=>x.scroll>x.width+1);
    if(bad.length) layouts.push({width,section,bad});
    if(width===1440||width===375) await page.screenshot({path:path.join(out,`${section}-${width}.png`),fullPage:true});
   }
  }
  assert.deepEqual(layouts, [], 'Unexpected horizontal overflow'); assert.deepEqual(errors, [], 'Browser runtime errors');
  await context.close();
  const auth=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});await auth.route('**/*',intercept);
  const ap=await auth.newPage();
  for(const width of [1440,375]){
   await ap.setViewportSize({width,height:1000});
   for(const route of ['login/','register/','forgot-password/','reset-password/preview/token/']){
    await ap.goto(base+'/dashboard/'+route,{waitUntil:'networkidle'});await ready(ap);
    assert.equal((await overflow(ap)).scroll, width, 'Auth page overflow');
    await ap.screenshot({path:path.join(out,`${route.split('/')[0]}-${width}.png`),fullPage:true});
   }
  }
  fs.writeFileSync(path.join(out,'initial-report.json'),JSON.stringify({layouts,errors},null,2));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
