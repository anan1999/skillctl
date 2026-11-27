// Optional browser check: SKILLCTL_PLAYWRIGHT points to a local Playwright install.
const {chromium} = require(process.env.SKILLCTL_PLAYWRIGHT || 'playwright');
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const {pathToFileURL} = require('url');
const report = path.resolve(process.argv[2] || '.demo/onboarding.html');
const output = path.resolve('.demo/onboarding-ui');
fs.mkdirSync(output, {recursive:true});
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1200,height:1000}, offline:true});
    const page = await context.newPage();
    const errors = [], external = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {if (/^https?:/.test(request.url())) external.push(request.url());});
    await page.goto(pathToFileURL(report).href);
    assert(await page.locator('#step-0').isVisible());
    assert(await page.locator('#step-1').isHidden());
    await page.locator('#next').click();
    assert(await page.locator('#step-1').isVisible());
    assert((await page.locator('#step-1 pre').innerText()).includes('SKILL.md'));
    await page.locator('#next').click();
    assert(await page.locator('#step-2').isVisible());
    assert((await page.locator('#step-2').innerText()).includes('Always include the reviewer'));
    assert((await page.locator('#step-2').innerText()).includes('release notes'));
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    await page.locator('#step-2 summary').click();
    assert(await page.locator('#step-2 pre').isVisible());
    await page.locator('#step-2 summary').click();
    await page.locator('#next').click();
    assert(await page.locator('#step-0').isVisible());
    await page.reload();
    assert(await page.locator('#step-0').isVisible());
    // Keyboard activation works without a pointing device.
    await page.locator('[data-step="2"]').focus();
    await page.keyboard.press('Enter');
    assert(await page.locator('#step-2').isVisible());
    await page.setViewportSize({width:390,height:844});
    for (let index = 0; index < 3; index++) {
      await page.locator(`[data-step="${index}"]`).click();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    }
    await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    await page.setViewportSize({width:1200,height:1000});
    await page.screenshot({path:path.resolve('docs/demo-preview.png'),fullPage:true});
    const nojs = await browser.newContext({javaScriptEnabled:false,offline:true});
    const fallback = await nojs.newPage();
    await fallback.goto(pathToFileURL(report).href);
    for (let index = 0; index < 3; index++) assert(await fallback.locator(`#step-${index}`).isVisible());
    assert.deepEqual(errors,[]);
    assert.deepEqual(external,[]);
    console.log(JSON.stringify({passed:true, checks:['three steps','actual diff and merged result',
      'expand full result','replay','offline reload','keyboard','390px layout','no-JavaScript fallback',
      'no external requests or page errors'], screenshots:['docs/demo-preview.png','.demo/onboarding-ui/mobile.png']}));
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
