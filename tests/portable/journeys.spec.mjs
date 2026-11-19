import {test,expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';
const password=(await readFile('/run/owner-password','utf8')).trim();
test('Given portable origin and absent erasure authority, public/CSRF/cross-origin requests are denied and erasure stays fail closed',async({page})=>{
 await page.goto('/');
 expect((await page.request.get('/api/v1/studies')).status()).toBe(401);
 expect((await page.request.get('/',{headers:{Host:'127.0.0.1:1'}})).status()).toBe(403);
 expect((await page.request.post('/api/v1/auth/session',{data:{username:'nobody',password:'invalid-test-only'}})).status()).toBe(403);
 expect((await page.request.post('/api/v1/auth/session',{headers:{Origin:'https://different.invalid'},data:{}})).status()).toBe(403);
 await signIn(page);await page.goto('/governance/');
 await expect(page.getByText('Study erasure is unavailable in this local workspace.')).toBeVisible();
 const result=await page.evaluate(async()=>{
  const {api}=await import('/static/u1/api.mjs');const before=await api('/studies');
  const workspace=document.querySelector('main').dataset.workspace;
  const study=before[0]??{studyId:crypto.randomUUID(),revision:1};let refusal;
  try{await api('/workspaces/'+workspace+'/erasure-cases',{method:'POST',data:{studyId:study.studyId,confirmation:study.studyId,revision:study.revision}});}
  catch(error){refusal={code:error.problem?.code,status:error.problem?.status};}
  const after=await api('/studies');return {refusal,preserved:JSON.stringify(before)===JSON.stringify(after)};
 });
 expect(result.refusal).toEqual({code:'GOVERNANCE_UNAVAILABLE',status:503});expect(result.preserved).toBe(true);
});

async function signIn(page){
 await page.goto('/');
 await page.getByTestId('signin-username').fill('installation-owner');
 await page.getByTestId('signin-password').fill(password);
 await page.getByTestId('signin-submit').click();
 await expect(page.getByTestId('new-study')).toBeVisible();
}
async function step(page,name){
 if(await page.getByTestId('mobile-step').isVisible())await page.getByTestId('mobile-step').selectOption(name);
 else await page.getByTestId('step-'+name).click();
}
async function publish(page){
 await page.getByTestId('draft-save').click();
 await expect(page.locator('#status')).toContainText('Saved');
 await step(page,'publish');
 await page.getByTestId('version-publish').click();
 const link=page.getByTestId('recruitment-link'); await expect(link).toBeVisible();
 return await link.getAttribute('href');
}
async function download(page,format,phrase){
 const delivery=page.getByRole('region',{name:'Evidence delivery'});
 await expect(delivery).toBeVisible();
 await page.getByRole('combobox',{name:'Evidence status'}).selectOption('submitted');
 await page.getByRole('button',{name:'Apply filters'}).click();
 await expect(delivery.getByRole('status').first()).toContainText('1 matching record');
 await delivery.getByRole('combobox',{name:'Export format'}).selectOption(format);
 await delivery.getByRole('button',{name:'Export…'}).click();
 const link=delivery.getByRole('link',{name:'Download '+format.toUpperCase()});
 await expect(link).toBeVisible();
 const event=page.waitForEvent('download');await link.click();const item=await event;
 expect(item.suggestedFilename()).toBe('study-evidence.'+format);
 const text=await readFile(await item.path(),'utf8');expect(text).toContain(phrase);
 expect(text.length).toBeGreaterThan(100);
}

test('Given fresh portable owner, browser authors/publishes card sort, participant submits, analysis/export works',async({page,browser,baseURL},info)=>{
 await signIn(page);
 const title='Portable cards '+crypto.randomUUID().slice(0,8);
 await page.getByTestId('new-study').click();await page.getByTestId('study-title').fill(title);
 await page.getByTestId('study-method').selectOption('card-sort');await page.getByTestId('study-create-submit').click();
 const cards=page.getByTestId('card-inventory');
 await cards.getByRole('textbox',{name:'Add card',exact:true}).fill('Pricing');await cards.getByRole('button',{name:'Add card',exact:true}).click();
 await cards.getByRole('textbox',{name:'Add card',exact:true}).fill('Support');await cards.getByRole('button',{name:'Add card',exact:true}).click();
 const groups=page.getByTestId('fixed-groups');
 await groups.getByRole('textbox',{name:'Add group',exact:true}).fill('Information');await groups.getByRole('button',{name:'Add group',exact:true}).click();
 await expect(groups.getByRole('textbox',{name:'Group name Information',exact:true})).toBeVisible();
 const recruitment=await publish(page);
 const visitor=await browser.newContext({baseURL,viewport:page.viewportSize(),hasTouch:!!info.project.use.hasTouch});
 const participant=await visitor.newPage();await participant.goto(recruitment);
 await participant.getByTestId('consent-checkbox').check();await participant.getByTestId('consent-accept').click();
 for(const label of ['Café','Books','Pricing','Support']){
  const more=participant.getByTestId('unsorted-more');if(await more.isVisible() && await more.getAttribute('aria-expanded')!=='true')await more.click();
  await participant.getByRole('button',{name:'Move '+label,exact:true}).click();
  const destination=participant.locator('#move-destination');await expect(destination).toBeVisible();
  const target=await destination.locator('option').evaluateAll(nodes=>nodes.find(node=>node.textContent.includes('Information')).value);
  await destination.selectOption(target);await participant.locator('#move-confirm').click();await expect(participant.locator('#move-dialog')).toBeHidden();
 }
 await participant.getByTestId('participant-review').click();await participant.getByTestId('participant-submit').click();
 await expect(participant.getByRole('heading',{name:'Response submitted'})).toBeVisible();await visitor.close();
 await step(page,'results');await page.getByTestId('analysis-run').click();
 await expect(page.getByTestId('analysis-summary')).toContainText('1 included');
 await download(page,'csv','Pricing');
 await page.screenshot({path:info.outputPath('card-results.png'),fullPage:true});
});

test('Given portable owner, browser authors/publishes tree, participant submits, analysis/export works',async({page,browser,baseURL},info)=>{
 await signIn(page);const title='Portable tree '+crypto.randomUUID().slice(0,8);
 await page.getByTestId('new-study').click();await page.getByTestId('study-title').fill(title);
 await page.getByTestId('study-method').selectOption('tree-test-v2');await page.getByTestId('study-create-submit').click();
 await page.getByRole('textbox',{name:'Resource name',exact:true}).fill('Plans');await page.getByTestId('u3-add-resource').click();
 await page.getByTestId('u3-link-resource').click();
 await step(page,'rules');await page.getByTestId('u3-add-task').click();await page.getByText('Task 1 · New task',{exact:true}).click();
 await page.getByRole('textbox',{name:'Task prompt'}).fill('Where are the plans?');
 await page.getByRole('group',{name:'Correct resources — private'}).getByRole('checkbox',{name:'Plans'}).check();
 await page.getByTestId('task-continue').click();const recruitment=await publish(page);
 const visitor=await browser.newContext({baseURL,viewport:page.viewportSize(),hasTouch:!!info.project.use.hasTouch});
 const participant=await visitor.newPage();await participant.goto(recruitment);
 await participant.getByTestId('consent-checkbox').check();await participant.getByTestId('consent-accept').click();
 await participant.getByRole('button',{name:'Start task'}).click();await participant.getByRole('button',{name:'Open Plans'}).click();
 await participant.getByRole('button',{name:'Choose this destination'}).click();
 await participant.getByRole('button',{name:'Review and submit'}).click();await participant.getByRole('button',{name:'Submit response'}).click();
 await expect(participant.getByRole('heading',{name:'Response submitted'})).toBeVisible();await visitor.close();
 await step(page,'results');await page.getByTestId('u3-analysis-run').click();
 await expect(page.getByRole('heading',{name:'Tree-test results'})).toBeVisible();await download(page,'json','Where are the plans?');
 await page.screenshot({path:info.outputPath('tree-results.png'),fullPage:true});
});
