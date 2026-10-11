// Run with NODE_PATH pointing to a Playwright installation. Optional CHROME_PATH and SCREENSHOT_DIR.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const server=http.createServer((req,res)=>{const file=path.join(root,new URL(req.url,'http://localhost').pathname.replace(/\/$/,'/index.html'));if(!file.startsWith(root+path.sep)){res.writeHead(403).end();return;}fs.readFile(file,(error,body)=>{if(error){res.writeHead(404).end();return;}const type={'.js':'text/javascript','.html':'text/html','.css':'text/css','.json':'application/json','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream';res.setHeader('Content-Type',type);res.end(body);});});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base='http://127.0.0.1:'+server.address().port;const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});try{
for(const [locale,expected,label] of [['zh-CN','zh-Hans','搜索历史'],['zh-TW','zh-Hant','搜尋歷史'],['en-US','en','Search history'],['fr-FR','en','Search history']]){
 for(const width of [390,1200]){const context=await browser.newContext({locale,viewport:{width,height:844},isMobile:width===390,hasTouch:width===390});const page=await context.newPage();let errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/photo-scout/v1/**',async route=>{const path=new URL(route.request().url()).pathname;let data={items:[]};if(path.endsWith('/status'))data={enabled:true,humanFreePreview:true,photoStyles:[{id:'nature',label:'Nature & calm',description:'Greenery, soft scenery and relaxed outdoor portraits.'}]};if(path.endsWith('/auth/me'))data={user:null,googleEnabled:true};await route.fulfill({json:data});});
 await page.goto(base+'/photo-scout-site/',{waitUntil:'domcontentloaded'});await page.waitForTimeout(1000);
 assert.equal(await page.locator('html').getAttribute('lang'),expected);assert.match(await page.locator('#search-history > summary').innerText(),new RegExp(label));assert.equal(await page.locator('#search-mode').inputValue(),'nearby');
 const prompt=page.locator('#prompt-query');await prompt.fill('Chengdu cafés');assert.equal(await prompt.inputValue(),'Chengdu cafés');
 await page.evaluate(()=>{const status=document.getElementById('message');status.textContent='Finding nearby places…';const foreign=document.createElement('div');foreign.className='comment-list';foreign.textContent='Night';document.body.append(foreign);});
 await page.waitForTimeout(100);assert.equal(await page.locator('.comment-list').last().textContent(),'Night');if(expected!=='en')assert.equal(await page.locator('#message').textContent(),expected==='zh-Hans'?'正在查找附近地点…':'正在查詢附近地點…');
 await page.evaluate(()=>document.getElementById('message').textContent='Another photo is being uploaded. This place has its own photo; you can upload it when that submission finishes.');await page.waitForTimeout(50);assert.match(await page.evaluate(()=>window.PhotoScoutI18n.source(document.getElementById('message'))),/^Another photo is being uploaded\./);
 await page.evaluate(()=>{const dialog=document.querySelector('.photo-studio');dialog.showModal();});await page.waitForTimeout(100);
 assert.equal(await page.locator('.studio-style input:checked').inputValue(),'natural');assert.equal(await page.locator('select[name=framing]').inputValue(),'auto');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 if(process.env.SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR,`photo-i18n-${locale}-${width}.png`)});assert.deepEqual(errors,[]);console.log(locale,width,expected,'main UI, dynamic statuses, selfie options, mobile width OK');await context.close();
}}
}finally{await browser.close();server.close();}})().catch(e=>{console.error(e);server.close();process.exit(1)});
