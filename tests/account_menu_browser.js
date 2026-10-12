const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const errors=[],guest=await browser.newPage({viewport:{width:1400,height:900}});guest.on('pageerror',e=>errors.push(e.message));
 await guest.route('**/api/account/session',r=>r.fulfill({json:{user:null,csrf:'test',registrationEnabled:true}}));
 let resetRequest;
 await guest.route('**/api/account/forgot-password',r=>{resetRequest=r.request().postDataJSON();return r.fulfill({json:{message:'If that email belongs to a confirmed account, a reset link has been sent recently. Check your inbox.'}});});
 await guest.goto('http://127.0.0.1:5000/charts');await guest.waitForFunction(()=>panes[0]?.historyLoaded);
 await guest.locator('#account-button').click();await guest.getByRole('menuitem',{name:'Forgot password?'}).click();
 await guest.locator('.account-form [name="email"]').fill('owner@example.test');await guest.getByRole('button',{name:'Send reset link',exact:true}).click();await guest.getByText('If that email belongs to a confirmed account, a reset link has been sent recently. Check your inbox.').waitFor();assert.equal(resetRequest.email,'owner@example.test');
 await guest.getByRole('button',{name:'Back to sign in'}).click();await guest.locator('.account-form [name="username"]').fill('owner@example.test');await guest.locator('.account-forgot').click();assert.equal(await guest.locator('.account-form [name="email"]').inputValue(),'owner@example.test');await guest.getByRole('button',{name:'Close',exact:true}).click();
 await guest.locator('#account-button').click();assert.equal(await guest.getByRole('menuitem',{name:'Admin dashboard'}).count(),0);await guest.mouse.click(150,200);assert.equal(await guest.locator('#account-dropdown').isVisible(),false);
 await guest.locator('#account-button').click();await guest.keyboard.press('Escape');assert.equal(await guest.locator('#account-button').getAttribute('aria-expanded'),'false');
 for(const role of ['member','admin']){
  const page=await browser.newPage({viewport:{width:1400,height:900}});page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/account/session',r=>r.fulfill({json:{user:{id:99999,name:'Menu test',role,email:'test@example.test'},csrf:'test',registrationEnabled:true}}));
  await page.goto('http://127.0.0.1:5000/charts');await page.waitForFunction(()=>panes[0]?.historyLoaded);await page.locator('#account-button').click();await page.getByRole('menuitem',{name:'Edit account'}).waitFor();assert.equal(await page.getByRole('menuitem',{name:'Admin dashboard'}).count(),role==='admin'?1:0);
  if(role==='admin'){assert.equal(await page.getByRole('menuitem',{name:'Admin dashboard'}).getAttribute('href'),'/api/account/admin');await page.screenshot({path:'reports/account-menu-20261012.png'});}
  await page.getByRole('menuitem',{name:'Edit account'}).click();await page.getByRole('button',{name:'Save profile',exact:true}).waitFor();
  assert.equal(await page.locator('.account-settings-footer a').count(),0);
  await page.getByRole('button',{name:'Security',exact:true}).click();await page.getByRole('button',{name:'Set up authenticator',exact:true}).waitFor();
  await page.getByRole('button',{name:'Close',exact:true}).click();await page.locator('#account-button').click();await page.getByRole('menuitem',{name:'Forgot password?'}).click();await page.getByRole('button',{name:'Send reset link',exact:true}).waitFor();
  await page.close();
 }
 assert.deepEqual(errors,[]);console.log('Account menu roles, outside/Escape dismissal, profile action and forgot-password request/prefill passed.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
