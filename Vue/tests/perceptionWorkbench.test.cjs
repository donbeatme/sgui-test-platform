// Run: node tests/perceptionWorkbench.test.cjs
// Requires npm dependencies and a Playwright Chromium installation.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const {spawn} = require('node:child_process')
const {chromium} = require('playwright')
const root = path.resolve(__dirname, '..')
const harness = path.join(root, '.perception-ui-test')
const port = Number(process.env.SGUI_TEST_PORT || 19081)
const base = `http://127.0.0.1:${port}`

async function main() {
  fs.mkdirSync(harness)
  fs.writeFileSync(path.join(harness, 'index.html'), '<html><body><div id="app"></div><script type="module" src="./main.ts"></script></body></html>')
  fs.writeFileSync(path.join(harness, 'main.ts'), `
import {createApp} from 'vue'
import {createPinia} from 'pinia'
import {createRouter,createMemoryHistory,RouterView} from 'vue-router'
import ArcoVue from '@arco-design/web-vue'
import ArcoVueIcon from '@arco-design/web-vue/es/icon'
import '@arco-design/web-vue/dist/arco.css'
import '../src/guicase-theme.css'
import Workbench from '../src/views/GuiCaseWorkbenchView.vue'
import {useProjectStore} from '../src/store/projectStore'
const app=createApp(RouterView),pinia=createPinia()
const router=createRouter({history:createMemoryHistory(),routes:[{path:'/',component:Workbench}]})
app.use(pinia);app.use(router);app.use(ArcoVue);app.use(ArcoVueIcon)
useProjectStore().setCurrentProject({id:1,name:'感知集成验证'} as any)
router.replace({path:'/',query:{job:'review-fixture'}}).then(()=>app.mount('#app'))
`)
  const server = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {cwd: root, stdio: 'pipe'})
  let browser,page
  try {
    for (let attempt = 0; attempt < 100; attempt++) {
      try { const response=await fetch(`${base}/.perception-ui-test/`); if(response.ok)break } catch {}
      if(attempt===99)throw new Error('Vite test server did not start')
      await new Promise(resolve=>setTimeout(resolve,150))
    }
    browser=await chromium.launch({headless:true,executablePath:process.env.SGUI_TEST_CHROME||undefined,args:['--no-sandbox']})
    page=await browser.newPage({viewport:{width:1500,height:1000}})
    const errors=[]
    page.on('pageerror',error=>{errors.push(error.message);console.error('Browser error:',error.message)})
    const fixture={id:'review-fixture',title:'图片控件检查验证',status:'awaiting_controls',stage:'请核对图片控件，再继续生成',revision:1,cases:[],issues:{errors:0,warnings:0,duplicates:[],detail:[]},notes:[],saved_ids:[],count:0,created_at:'2026-10-28T12:00:00Z',images:[{name:'demo.png',url:'/demo.svg'}],control_images:[{unit_id:'U1',image_name:'demo.png',page:'演示页面',legacy:false,controls:[{id:'U1-C01',name:'登录按钮',text:'Login',type:'button',state:'可见',bbox:[.2,.3,.4,.2],region:'main_workspace',origin:'perception',excluded:false,case_ids:[],case_names:[]}]}],progress:{phase:'controls',batch_total:0,batch_done:0,modules:[],vision:{image_total:1,image_done:1,batch_total:1,batch_done:1}},image_analysis:'perception_v1'}
    let submitted
    await page.route('**/demo.svg',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="400"><rect width="800" height="400" fill="#f0f4ff"/><rect x="160" y="120" width="320" height="80" rx="8" fill="#2563eb"/><text x="260" y="173" font-size="36" fill="white">Login</text></svg>'}))
    await page.route('**/api/**',async route=>{
      const url=new URL(route.request().url())
      let data
      if(url.pathname.endsWith('/continue-controls/')){
        submitted=route.request().postDataJSON()
        fixture.status='queued';fixture.stage='控件已核对，等待生成用例';fixture.revision++
        data=fixture
      }else if(url.pathname.includes('/jobs/'))data=fixture
      else data={settings:{rules:[],prompts:{},stages:{}},models:[],documents:[],knowledge_bases:[],jobs:[],can_configure:false}
      await route.fulfill({json:{status:'success',data}})
    })
    await page.goto(`${base}/.perception-ui-test/`)
    const tabs=page.locator('.gc-panel > .arco-tabs > .arco-tabs-nav .arco-tabs-tab-title')
    await tabs.filter({hasText:'图片控件检查'}).waitFor()
    assert.deepEqual((await tabs.allTextContents()).slice(0,3),['需求与图片','图片控件检查','用例结果'])
    await page.getByPlaceholder('无文字图标可填写名称').waitFor()
    await page.getByPlaceholder('无文字图标可填写名称').fill('Sign in')
    // Switching tabs must retain the unsaved control edit and keep the result pane independent.
    await tabs.filter({hasText:'用例结果'}).click()
    await page.waitForFunction(()=>document.querySelector('.gc-panel > .arco-tabs .arco-tabs-tab-active')?.textContent==='用例结果')
    await tabs.filter({hasText:'图片控件检查'}).click()
    assert.equal(await page.getByPlaceholder('无文字图标可填写名称').inputValue(),'Sign in')
    await page.getByText('已核对本图识别结果',{exact:true}).click()
    if(process.env.SGUI_TEST_SCREENSHOT)await page.screenshot({path:process.env.SGUI_TEST_SCREENSHOT,fullPage:true})
    await page.getByRole('button',{name:'按核对结果继续生成',exact:true}).click()
    await page.waitForFunction(()=>document.querySelector('.gc-panel > .arco-tabs .arco-tabs-tab-active')?.textContent==='用例结果')
    assert.equal(submitted.images[0].reviewed,true)
    assert.equal(submitted.images[0].controls[0].text,'Sign in')
    assert.deepEqual(errors,[])
    console.log('PASS: tab order, standalone review, unsaved edit retention and continue-to-results')
  } catch(error) {
    if(page){console.error('Page:',await page.locator('body').innerText());await page.screenshot({path:process.env.SGUI_TEST_SCREENSHOT||'/tmp/sgui-perception-ui-failure.png'})}
    throw error
  } finally {
    if(browser)await browser.close()
    server.kill('SIGTERM')
    fs.rmSync(harness,{recursive:true,force:true})
  }
}
main().catch(error=>{console.error(error);process.exitCode=1})
