// Native Chromium trust and rendering fallback. Never disables certificate checks.
const {chromium}=require('playwright');
const {spawnSync}=require('node:child_process');
const fs=require('node:fs');
const readline=require('node:readline');
const robotCode="import sys,json;from urllib.robotparser import RobotFileParser;d=json.load(sys.stdin);r=RobotFileParser();r.parse(d['text'].splitlines());print(json.dumps({'allowed':r.can_fetch(d['ua'],d['url']),'delay':r.crawl_delay(d['ua']) or r.crawl_delay('*') or 1}))";
const ua=process.env.FEES_CRAWLER_UA||'FeeDisclosureResearch/1.2 (user-requested public disclosure review)';
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({userAgent:ua});
 let page;const robots=new Map(),last=new Map();
 const inputs=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
 for await(const line of inputs){
  if(!line.trim())continue;const task=JSON.parse(line);page=await context.newPage();let result={url:task.url};
  try{
   const origin=new URL(task.url).origin;
   if(!robots.has(origin)){
    const r=await page.goto(origin+'/robots.txt',{waitUntil:'domcontentloaded',timeout:15000});
    if(r.status()!==404 && r.status()>=400)throw Error('robots.txt HTTP '+r.status());
    const text=r.status()===404?'':await page.locator('body').innerText();
    if(r.status()!==404 && /captcha|验证码|安全验证|access denied/i.test(text))throw Error('robots.txt 返回验证页面');
    robots.set(origin,text);last.set(origin,Date.now());
   }
   const p=spawnSync(task.python,['-X','utf8','-c',robotCode],{input:JSON.stringify({text:robots.get(origin),ua,url:task.url}),encoding:'utf8'});
   if(p.status!==0)throw Error('robots 解析失败: '+p.stderr);
   const rule=JSON.parse(p.stdout);if(!rule.allowed)throw Error('robots.txt 不允许采集此路径');
   await pause(Math.max(0,Math.max(1,rule.delay)*1000-(Date.now()-(last.get(origin)||0))));
   last.set(origin,Date.now());
   if(/\.(pdf|xlsx?)(?:\?|$)/i.test(task.url)){
    const binary=await page.evaluate(async url=>{
     const r=await fetch(url,{signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error('HTTP '+r.status);
     const b=new Uint8Array(await r.arrayBuffer());if(b.length>15000000)throw Error('文件超过15MB');
     let str='';for(let i=0;i<b.length;i+=8192)str+=String.fromCharCode(...b.subarray(i,i+8192));
     return {binary_b64:btoa(str),final_url:r.url,http_status:r.status,content_type:r.headers.get('content-type')||'application/octet-stream'};
    },task.url);
    result={...result,...binary};await page.close();process.stdout.write(JSON.stringify(result)+'\n');continue;
   }
   const response=await page.goto(task.url,{waitUntil:'domcontentloaded',timeout:15000});
   if(response.status()>=400)throw Error('HTTP '+response.status());
   let settled=true;
   try{await page.waitForLoadState('networkidle',{timeout:5000});}catch(e){settled=false;}
   const text=await page.locator('body').innerText();
   if(/^(验证|安全验证)|captcha|人机验证|访问过于频繁/i.test((await page.title())+' '+text.slice(0,300)))throw Error('页面要求访问验证，需人工处理');
   result={...result,final_url:page.url(),http_status:response.status(),content_type:response.headers()['content-type']||'text/html',html:await page.content(),text,settled};
   if(task.screenshotsBase){
    result.screenshots=[];
    const imgs=page.locator('img');
    for(let i=0;i<await imgs.count();i++){
     const im=imgs.nth(i),size=await im.evaluate(el=>({w:el.naturalWidth,h:el.naturalHeight,src:el.currentSrc}));
     if(size.w>500&&size.h>600&&await im.isVisible()){
      const path=task.screenshotsBase+'-'+i+'.png';await im.screenshot({path,timeout:10000});
      result.screenshots.push({path,...size});
     }
    }
   }
  }catch(e){result.error=e.message;}
  await page.close();process.stdout.write(JSON.stringify(result)+'\n');
 }
 await browser.close();
}
main().catch(e=>{process.stderr.write(e.stack);process.exitCode=1;});
