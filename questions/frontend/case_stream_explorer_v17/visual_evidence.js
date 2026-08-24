async (page)=>{
  const evidenceDir=__MODELDIAL_EVIDENCE_DIR_JSON__;
  const targetBase=page.url().split("?",1)[0];
  const states=[];
  const reset=async(width,height)=>{await page.setViewportSize({width,height});await page.emulateMedia({reducedMotion:"reduce"});await page.goto("about:blank");await page.goto(targetBase,{waitUntil:"domcontentloaded"});await page.waitForTimeout(120)};
  const screenshot=async(id,width,height,action)=>{let demonstrated=true,error="";try{await reset(width,height);if(action)await action()}catch(value){demonstrated=false;error=String(value).slice(0,500)}const filename=`${id}.png`;await page.screenshot({path:`${evidenceDir}/${filename}`,animations:"disabled",fullPage:false});states.push({id,filename,width,height,demonstrated,error})};
  const setQuery=async value=>{await page.locator("#search").fill(value);await page.waitForTimeout(160)};
  const toggle=async id=>{await setQuery(id);await page.getByRole("button",{name:`Toggle selection ${id}`,exact:true}).click()};
  const openInspector=async id=>{await setQuery(id);const direct=page.locator(`[data-open-id='${id}']`);if(await direct.count())await direct.first().click();else await page.locator(`[data-case-id='${id}'] .case-main`).first().click();await page.waitForTimeout(40)};
  await screenshot("default_desktop",1440,900);
  await screenshot("default_tablet",768,1024);
  await screenshot("default_mobile",390,844);
  await screenshot("selected_saving",1440,900,async()=>{await toggle("CASE-012");await page.getByRole("button",{name:"Mark investigating",exact:true}).click();await page.waitForTimeout(12)});
  await screenshot("failure",1440,900,async()=>{await toggle("CASE-012");await toggle("CASE-013");await page.getByRole("button",{name:"Mark resolved",exact:true}).click();await page.waitForTimeout(230)});
  await screenshot("desktop_inspector",1440,900,async()=>{await openInspector("CASE-005")});
  await screenshot("mobile_inspector",390,844,async()=>{await openInspector("CASE-005")});
  return{schema_version:"frontend_visual_evidence_v1",states};
}
