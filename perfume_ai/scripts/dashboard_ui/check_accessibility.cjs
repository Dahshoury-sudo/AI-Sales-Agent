const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {intercept,base,ready}=require('./check_layout.cjs');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const axe=await fetch('https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.3/axe.min.js').then(r=>r.text());
 const results=[];
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  await context.route('**/*',intercept);
  await context.addInitScript(()=>{localStorage.setItem('access_token','preview');localStorage.setItem('perfume_api_key','preview');});
  const page=await context.newPage();await page.goto(base+'/dashboard/',{waitUntil:'networkidle'});
  async function check(frame,name){
   await ready(frame);
   await frame.addScriptTag({content:axe});
   const v=await frame.evaluate(async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary})).slice(0,8)}));});
   if(v.length)results.push({name,violations:v});
  }
  for(const s of ['overview','analytics','products','orders','handoff','chat_history','chat_demo','faq','post_rules','bulk_import','settings','meta','notifications']){
   await page.evaluate(s=>showSection(s),s);await ready(page);
   const el=page.locator('#iframe-'+s);
   if(await el.count()){
    const frame=await (await el.elementHandle()).contentFrame();await frame.waitForLoadState('domcontentloaded');
    if(s==='handoff'||s==='chat_history'){await frame.locator('.conv-item').first().click();await ready(frame);}
    await check(frame,s+' iframe');
   }else await check(page,s);
  }
  for(const [s,fn] of [['products','openEditModal(1)'],['faq','openFaqModal()'],['post_rules','openPostRuleModal()']]){
   await page.evaluate(s=>showSection(s),s);await ready(page);await page.evaluate(fn=>eval(fn),fn);await ready(page);await check(page,s+' dialog');await page.keyboard.press('Escape');
  }
  await page.setViewportSize({width:375,height:900});await page.evaluate(()=>showSection('overview'));await check(page,'mobile overview');
  await page.locator('.mobile-toggle').click();await check(page,'mobile drawer');await page.keyboard.press('Escape');
  await page.evaluate(()=>showSection('handoff'));await ready(page);
  const mobileChat=await(await page.locator('#iframe-handoff').elementHandle()).contentFrame();
  await mobileChat.locator('#btn-resolve').hover();await check(mobileChat,'mobile conversation and resolve hover');
  const plain=await browser.newContext({viewport:{width:375,height:900},reducedMotion:'reduce'});await plain.route('**/*',intercept);
  const ap=await plain.newPage();
  for(const r of ['login/','register/','forgot-password/','reset-password/preview/token/']){
   await ap.goto(base+'/dashboard/'+r,{waitUntil:'networkidle'});await check(ap,r);
  }
  require('node:fs').writeFileSync(require('node:path').join(require('./check_layout.cjs').out, 'accessibility-report.json'), JSON.stringify(results,null,2));
  require('node:assert/strict').deepEqual(results, [], 'Accessibility violations');
  console.log('PASS: desktop, mobile, dialogs and account-page accessibility');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
