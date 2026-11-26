// Synthetic fixture only. No user logs or skills are changed.
const {chromium}=require(process.env.SKILLCTL_PLAYWRIGHT||'playwright');
const {spawn,spawnSync}=require('child_process'),fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'skillctl-usage-'));
const setup=`import sys
from quality_demo import create_fixture
from skill_dashboard import make_server
server=make_server(create_fixture(sys.argv[1]))
print('http://127.0.0.1:'+str(server.server_port),flush=True)
server.serve_forever()
`;
(async()=>{
  const server=spawn(process.env.SKILLCTL_PYTHON||'python',['-B','-u','-X','utf8','-c',setup,root],{windowsHide:true,stdio:['ignore','pipe','pipe']});
  let browser;
  try {
    const url=await new Promise((resolve,reject)=>{
      let out='',err='';
      const timer=setTimeout(()=>reject(Error('Startup timeout: '+err)),20000);
      server.stderr.on('data',x=>err+=x);
      server.stdout.on('data',x=>{out+=x;const m=out.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});
      server.on('exit',code=>{clearTimeout(timer);reject(Error('Server exit '+code+': '+err));});
    });
    browser=await chromium.launch({channel:'chrome',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],writes=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(r.method()!=='GET')writes.push(r.url());});
    const ready=()=>page.waitForFunction(()=>!document.querySelector('#refresh').disabled);
    const choose=async name=>{await page.getByRole('button',{name:'檢視 '+name,exact:true}).click();await ready();};
    const period=async days=>{await page.locator('#usage-'+days).click();await ready();};
    await page.goto(url);await ready();await page.locator('#show-library').click();
    await choose('failure-triage');
    assert.equal(await page.locator('#usage-reads').innerText(),'3');
    assert.equal(await page.locator('#usage-conversations').innerText(),'2');
    assert.match(await page.locator('#quality-status').innerText(),/4\/4.*通過/);
    assert.match(await page.locator('#usage-meta').innerText(),/依技能名稱彙總/);
    assert.match(await page.locator('#usage-status').innerText(),/示範資料/);
    await period(30);assert.equal(await page.locator('#usage-reads').innerText(),'8');
    assert.equal(await page.locator('#usage-conversations').innerText(),'4');
    assert.equal(await page.locator('#usage-30').getAttribute('aria-pressed'),'true');
    await page.locator('.usage').screenshot({path:path.join(root,'usage-desktop.png')});
    await choose('untested-demo');assert.equal(await page.locator('#usage-reads').innerText(),'0');
    assert.match(await page.locator('#quality-status').innerText(),/未測試/);
    await page.locator('#target').selectOption('1');await ready();await choose('failure-triage');
    assert.match(await page.locator('#usage-status').innerText(),/尚未支援/);
    assert.equal(await page.locator('#usage-counts').isVisible(),false);
    await page.locator('#target').selectOption('0');await ready();await choose('failure-triage');
    const report=path.join(root,'usage.json'),original=fs.readFileSync(report);
    fs.writeFileSync(report,'{bad');await period(7);
    assert.match(await page.locator('#usage-status').innerText(),/無法讀取/);
    assert.equal(await page.locator('#usage-counts').isVisible(),false);
    assert.match(await page.locator('#quality-status').innerText(),/通過/);
    fs.writeFileSync(report,original);await period(30);
    await page.reload();await ready();await page.locator('#show-library').click();await choose('failure-triage');
    assert.equal(await page.locator('#usage-reads').innerText(),'3');
    await page.setViewportSize({width:390,height:844});
    await period(30);assert.equal(await page.locator('#usage-reads').innerText(),'8');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.locator('.usage').screenshot({path:path.join(root,'usage-mobile.png')});
    assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
    console.log(JSON.stringify({passed:true,artifacts:root,checks:['7/30 counts','quality coexistence','zero vs unavailable','Pi unsupported','corrupt recovery','reload','mobile','read only']}));
  } finally {
    if(browser)await browser.close();
    if(process.platform==='win32')spawnSync('taskkill',['/PID',String(server.pid),'/T','/F'],{windowsHide:true});
    else server.kill();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
