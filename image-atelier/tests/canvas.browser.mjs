import assert from 'node:assert/strict';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const origin=process.env.CANVAS_TEST_ORIGIN||'http://127.0.0.1:18793';
const browser=await chromium.launch({headless:true,channel:'msedge'});
try {
 const page=await browser.newPage({viewport:{width:1400,height:900}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 // Keep the harness isolated from the application's API and saved drafts.

 let release,releaseLate;const held=new Promise(r=>release=r),late=new Promise(r=>releaseLate=r);
 await page.route('**/api/assets/*/image*',async route=>{
  const url=new URL(route.request().url()),id=url.pathname.split('/').at(-2);
  if(id==='slow')await held;
  if(id==='late')await late;
  if(id==='bad'&&!url.search)return route.abort();
  await route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="${['red','late'].includes(id)?'red':'blue'}"/></svg>`});
 });
 // Warm up Vite's dependency optimizer using the real entry module.
 await page.request.get(origin+'/src/main.jsx');
 await page.route('**/canvas-test',r=>r.fulfill({contentType:'text/html',body:`<html><meta charset="utf-8"><title>Canvas regression</title><div id="root"></div><script type="module">
import React from '/node_modules/.vite/deps/react.js';
import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
import Canvas from '/src/Canvas.jsx';import ImageViewer from '/src/ImageViewer.jsx';import '/src/styles.css';
const {useState}=React;
function App(){const [asset,setAsset]=useState({id:'red',width:64,height:64}),[zoom,setZoom]=useState(0),[pan,setPan]=useState([0,0]),[open,setOpen]=useState(false);window.change=id=>setAsset(id?{id,width:64,height:64}:null);return React.createElement(React.Fragment,null,React.createElement(Canvas,{asset,label:'結果',zoom,setZoom,pan,setPan,onOpen:()=>setOpen(true)}),open&&React.createElement(ImageViewer,{asset,onClose:()=>setOpen(false)}));}ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App));
</script></html>`}));
 await page.goto(origin+'/canvas-test');
 const pixel=channel=>page.waitForFunction(c=>document.querySelector('canvas')?.getContext('2d').getImageData(0,0,1,1).data[c]===255,channel);
 await pixel(0);
 assert.equal(await page.title(),'Canvas regression');
 await page.evaluate(()=>window.change('slow'));
 await page.getByRole('status').waitFor();
 assert.equal(await page.locator('canvas').count(),0,'old bitmap must disappear while replacement is loading');
 release();await pixel(2);
 console.log('PASS same-sized replacement loads without user interaction');
 await page.evaluate(()=>window.change('late'));
 await page.getByRole('status').waitFor();
 await page.evaluate(()=>window.change('blue'));
 await pixel(2);releaseLate();await page.waitForTimeout(100);
 assert.equal(await page.locator('canvas').evaluate(c=>c.getContext('2d').getImageData(0,0,1,1).data[2]),255);
 console.log('PASS stale load cannot overwrite newer result');
 await page.evaluate(()=>window.change('bad'));
 await page.getByRole('alert').waitFor();
 assert.equal(await page.locator('canvas').count(),0);
 await page.getByRole('button',{name:'再読み込み',exact:true}).click();await pixel(2);
 console.log('PASS failed image has explicit retry and recovers');
 await page.getByRole('button',{name:'結果画像を大きく表示'}).click();
 await page.getByRole('dialog').waitFor();
 await page.getByRole('button',{name:'閉じる',exact:true}).click();
 await pixel(2);
 await page.getByRole('button',{name:'拡大',exact:true}).click();await pixel(2);
 await page.screenshot({path:join(tmpdir(),'canvas-after.png')});
 await page.setViewportSize({width:390,height:844});await pixel(2);
 await page.evaluate(()=>window.change(null));await page.getByText('ここに結果画像が表示されます').waitFor();
 assert.deepEqual(errors,[]);
 console.log('PASS viewer return, zoom, mobile, clear; no JavaScript errors');
} finally {await browser.close();}
