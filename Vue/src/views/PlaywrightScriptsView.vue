<template>
  <div class="scripts-page">
    <header><div><p class="eyebrow">SGUI · 自动化回归</p><h1>Playwright 脚本</h1><p>从用例生成脚本，或保存 LLM 执行代码；直接运行并追溯每次结果。</p></div>
      <a-space><a-button @click="router.push('/testcases')">去执行用例</a-button><a-button @click="router.push('/test-executions')">执行历史</a-button><a-button :loading="loading" @click="load">刷新</a-button></a-space></header>
    <a-empty v-if="!projectId" description="请先选择项目" />
    <template v-else>
      <a-alert>在用例管理点击「生成脚本」，新脚本独立复跑一次，执行完成且无错误即显示「复跑通过」，可以运行或下载。当前不额外要求覆盖审查或连续两次通过。</a-alert>
      <a-alert v-if="error" type="error" style="margin-top:12px">{{ error }}</a-alert>
      <a-table class="script-table" :data="scripts" :loading="loading" :pagination="{pageSize:10}" row-key="id" :scroll="{x:1000}">
        <template #columns>
          <a-table-column title="脚本 / 来源用例" :width="290"><template #cell="{record}"><a-link @click="open(record.id)">{{ record.name }}</a-link><div class="muted">用例 #{{ record.testcase_id || '已删除' }} · 脚本 #{{record.id}} · {{ record.generation_mode === 'case' ? '用例直接生成' : '执行记录生成' }}</div></template></a-table-column>
          <a-table-column title="状态" :width="220"><template #cell="{record}"><a-tag :color="record.usable ? 'green' : activeBuild(record) ? 'blue' : 'orange'">{{ state(record) }}</a-tag><div class="muted" v-if="record.usable">独立复跑已完成，无执行错误</div><a-link v-if="!record.usable && !activeBuild(record)" @click="open(record.id)">查看原因 / 重新生成</a-link></template></a-table-column>
          <a-table-column title="保存时间" :width="180"><template #cell="{record}">{{ new Date(record.created_at).toLocaleString() }}</template></a-table-column>
          <a-table-column title="操作" :width="350"><template #cell="{record}"><a-space wrap>
            <a-button size="small" @click="open(record.id)">代码与记录</a-button>
            <a-button size="small" type="primary" :disabled="activeBuild(record) || (!record.usable && (record.build || !record.latest || record.latest.issues.length))" @click="prepareRun(record.id)">{{record.usable||record.build?'运行':'验证运行'}}</a-button>
            <a-button v-if="activeBuild(record)" size="small" status="warning" @click="cancelBuild(record.id)">停止生成</a-button><a-button size="small" :disabled="!record.source_execution_id" @click="showReport(record.source_execution_id)">来源报告</a-button>
            <a-popconfirm content="移除该脚本？历史执行报告和对应版本会保留。" :disabled="activeBuild(record)" @ok="remove(record.id)"><a-button size="small" status="danger" type="text" :disabled="activeBuild(record)">删除</a-button></a-popconfirm>
          </a-space></template></a-table-column>
        </template>
        <template #empty><a-empty description="还没有脚本。请在用例管理点击「生成脚本」，或执行用例时勾选保存。" /></template>
      </a-table>
    </template>

    <a-drawer :visible="!!detail" :width="'min(1120px, 96vw)'" :footer="false" @cancel="closeDetail">
      <template #title>{{ detail?.name }} · 代码与运行记录</template>
      <template v-if="detail">
        <a-switch v-model="advanced" checked-text="技术详情" unchecked-text="简洁视图" style="margin-bottom:12px" />
        <a-alert v-if="detail.error" type="error">{{ detail.error }}</a-alert>
        <a-alert v-if="detail.build" :type="detail.build.status==='ready'?'success':activeBuild(detail)?'info':'warning'" style="margin-top:12px">
          {{ state(detail) }}
          <p v-if="advanced">{{ buildState(detail.build.status) }} · 第 {{ detail.build.attempt }} / {{ detail.build.max_attempts }} 轮</p>
          <p v-if="advanced && detail.build.diagnostic_only">本轮为诊断采集，不计为业务通过。</p>
          <p v-if="detail.build.error">{{ detail.build.error }}</p>
          <a-space><a-button v-if="activeBuild(detail)" size="small" status="warning" @click="cancelBuild(detail.id)">停止生成与试跑</a-button><a-button v-if="detail.build.execution_id" size="small" @click="showReport(detail.build.execution_id)">本轮报告</a-button></a-space>
        </a-alert>
        <a-space wrap class="toolbar">
          <a-select v-if="advanced && detail.versions?.length" v-model="selectedVersion" style="width:240px" @change="selectVersion"><a-option v-for="v in detail.versions" :key="v.id" :value="v.id">v{{v.number}} · {{v.origin==='guided'?'逐步生成':v.origin==='ai_case'?'用例直接生成':v.origin==='ai'?'执行记录生成':'人工修订'}} · {{v.verified?'复跑通过':'未验证'}}</a-option></a-select>
          <a-button :disabled="!selectedVersion || (currentVersion?.origin==='guided' && !currentVersion?.verified)" @click="download('py')">下载 .py</a-button>
          <a-button type="primary" :disabled="!selectedVersion || (currentVersion?.origin==='guided' && !currentVersion?.verified)" @click="download('zip')">下载运行包 ZIP</a-button>
          <a-button v-if="advanced" :loading="busy" :disabled="activeBuild(detail) || ['recording','queued','generating'].includes(detail.status)" @click="regenerate">重新整理为新版本</a-button>
          <a-button v-if="detail.generation_mode==='case'" type="primary" :disabled="dirty || activeBuild(detail) || ['queued','generating'].includes(detail.status)" @click="buildVisible=true">重新生成</a-button>
          <a-button @click="refreshDetail">刷新状态</a-button>
        </a-space>
        <a-alert v-if="!detail.versions?.length" type="info">{{ state(detail) }}。可关闭本页继续其他操作，稍后刷新查看代码。</a-alert>
        <template v-else-if="advanced || detail.usable">
          <a-alert v-if="currentVersion?.issues.length" type="error" title="以下问题需要修复后才能运行"><div v-for="item in currentVersion.issues" :key="item">• {{item}}</div><p>可点击「重新整理为新版本」自动修订，或编辑代码后保存；旧版本会保留。</p></a-alert>
          <a-alert v-if="advanced && currentVersion?.warnings?.length" type="warning" title="运行提示" class="review-notes"><div v-for="item in currentVersion.warnings" :key="item">• {{item}}</div></a-alert>
          <a-collapse class="basis"><a-collapse-item header="来源用例与冻结预期"><p>{{detail.snapshot?.precondition}}</p><p v-for="step in detail.snapshot?.steps" :key="step.step_number"><b>{{step.step_number}}. {{step.description}}</b><br/>预期：{{step.expected_result}}</p></a-collapse-item></a-collapse>
          <label>Playwright Python 代码</label><a-textarea v-model="code" :readonly="!advanced" class="code-editor" :auto-size="{minRows:15,maxRows:26}" spellcheck="false" />
          <template v-if="advanced"><label>默认参数（JSON）</label><a-textarea v-model="parameters" class="code-editor" :auto-size="{minRows:3,maxRows:8}" />
          <a-space class="toolbar"><a-button type="primary" :loading="busy" :disabled="activeBuild(detail) || !dirty" @click="save">保存为新版本</a-button><a-button :disabled="!dirty" @click="resetEditor">撤销未保存修改</a-button></a-space></template>
          <a-button :disabled="activeBuild(detail) || dirty || !currentVersion || currentVersion.issues.length>0 || (currentVersion.origin==='guided' && !currentVersion.verified)" @click="prepareRun(detail.id, selectedVersion)">运行脚本</a-button>
        </template>
        <h3>脚本运行记录</h3>
        <a-table :data="detail.runs || []" :pagination="{pageSize:5}" row-key="id">
          <template #columns><a-table-column title="版本" data-index="version"/><a-table-column title="状态"><template #cell="{record}">{{runState(record.status)}}</template></a-table-column><a-table-column title="时间"><template #cell="{record}">{{new Date(record.created_at).toLocaleString()}}</template></a-table-column><a-table-column title="操作"><template #cell="{record}"><a-space><a-button size="small" @click="showReport(record.execution_id)">报告</a-button><a-button v-if="['pending','running'].includes(record.status)" size="small" status="warning" @click="stop(record.id)">停止</a-button></a-space></template></a-table-column></template>
        </a-table>
      </template>
    </a-drawer>

    <a-modal v-model:visible="runVisible" title="直接运行 Playwright 脚本" :width="680" :ok-loading="busy" @before-ok="startRun" @cancel="runVisible=false">
      <p>{{runScript?.name}} · v{{runVersion?.number}}</p>
      <a-alert>本次直接执行保存的代码，不调用 LLM。每次使用独立浏览器环境；页面变化或登录要求仍可能导致失败或阻塞。</a-alert>
      <a-alert v-if="runVersion?.warnings?.length" type="warning" title="运行前请留意" class="review-notes"><div v-for="item in runVersion.warnings" :key="item">• {{item}}</div></a-alert>
      <a-form layout="vertical" style="margin-top:16px"><a-form-item label="执行器"><a-select v-model="workerId" placeholder="选择在线执行器"><a-option v-for="w in workers" :key="w.worker_id" :value="w.worker_id">{{w.worker_id}}</a-option></a-select></a-form-item>
        <a-form-item label="运行参数（JSON）"><a-textarea v-model="runParameters" :auto-size="{minRows:4,maxRows:10}" class="code-editor"/><template #extra>默认沿用项目地址；如需覆盖，添加 base_url。其他未填写参数沿用当前版本默认值。</template></a-form-item>
        <a-form-item label="总超时（秒）"><a-input-number v-model="timeout" :min="30" :max="1800"/></a-form-item>
        <a-checkbox v-model="headless">无头模式（不显示浏览器）</a-checkbox>
      </a-form>
      <a-alert v-if="!workers.length" type="warning">没有在线脚本执行器，请启动本地项目后刷新。</a-alert>
    </a-modal>
    <GenerateScriptModal v-if="projectId && detail" v-model:visible="buildVisible" :project-id="projectId" :script-id="detail.id" :name="detail.name" @submitted="id=>{void open(id);void load();}" />
    <TestExecutionReportModal v-if="projectId" v-model:visible="reportVisible" :execution-id="reportId" :current-project-id="projectId" />
  </div>
</template>

<script setup lang="ts">
import {computed, onMounted, onUnmounted, ref, watch} from 'vue';
import {useRouter, useRoute} from 'vue-router';
import {Message, Modal} from '@arco-design/web-vue';
import request from '@/utils/request';
import {useProjectStore} from '@/store/projectStore';
import GenerateScriptModal from '@/components/testcase/GenerateScriptModal.vue';
import TestExecutionReportModal from '@/components/testcase/TestExecutionReportModal.vue';
interface Version {id:number;number:number;code:string;parameters:Record<string,unknown>;issues:string[];warnings:string[];origin:string;verified:boolean}
interface Run {id:number;version:number;execution_id:number;status:string;created_at:string}
interface Build {status:string;attempt:number;max_attempts:number;error:string;execution_id:number|null;diagnostic_only?:boolean}
interface Script {usable:boolean;delivery_status?:string;build?:Build;id:number;name:string;generation_mode:string;testcase_id:number|null;source_execution_id:number|null;status:string;error:string;created_at:string;latest:Version|null;versions?:Version[];runs?:Run[];snapshot?:{precondition:string;steps:{step_number:number;description:string;expected_result:string}[]}}
interface Worker {worker_id:string;supports_headed:boolean}
const router=useRouter(), route=useRoute(), store=useProjectStore(), projectId=computed(()=>store.currentProjectId);
const scripts=ref<Script[]>([]), workers=ref<Worker[]>([]), loading=ref(false), error=ref(''), busy=ref(false);
const detail=ref<Script|null>(null), selectedVersion=ref<number>(), code=ref(''), parameters=ref('{}');
const currentVersion=computed(()=>detail.value?.versions?.find(v=>v.id===selectedVersion.value));
const dirty=computed(()=>!!currentVersion.value && (code.value!==currentVersion.value.code || parameters.value!==JSON.stringify(currentVersion.value.parameters,null,2)));
const runVisible=ref(false), runScript=ref<Script>(), runVersion=ref<Version>(), workerId=ref(''), runParameters=ref('{}'), headless=ref(false), timeout=ref(600);
const buildVisible=ref(false);
const advanced=ref(false);
const reportVisible=ref(false), reportId=ref<number|null>(null);
const base=()=>`/projects/${projectId.value}/playwright-scripts/`;
const unwrap=(data:any)=>data?.data ?? data;
const message=(e:any)=>e?.response?.data?.message || e?.response?.data?.error || e?.error || e?.message || JSON.stringify(e?.response?.data || e);
const state=(s:Script)=>activeBuild(s)||['recording','queued','generating','observing','validating'].includes(s.status)?'生成中':s.usable?'复跑通过':s.build?.status==='cancelled'?'已取消':s.build?'未生成成功':'待验证';
const activeBuild=(s:Script)=>!!s.build&&['pending_observation','observing','queued','generating','validating'].includes(s.build.status);
const buildState=(s:string)=>({pending_observation:'等待读取页面',observing:'正在读取真实页面',queued:'等待模型生成',generating:'正在生成或修订代码',validating:'正在独立复跑',ready:'复跑通过',draft:'代码检查未通过',failed:'复跑未通过',blocked:'执行阻塞',not_applicable:'场景未触发，不计为通过',cancelled:'已停止',error:'生成未完成'}[s]||s);
async function cancelBuild(id:number){try{await request.post(base()+id+'/cancel-build/');await load();if(detail.value?.id===id)await refreshDetail();Message.success('已停止实测生成，已保存的代码与报告保留。');}catch(e){Message.error(message(e));}}
const runState=(s:string)=>({pending:'等待执行',running:'执行中',pass:'通过',fail:'失败',skip:'未触发／不适用',blocked:'阻塞',error:'系统错误',cancelled:'已取消'}[s]||s);
async function load(){if(!projectId.value)return;const id=projectId.value;loading.value=true;try{const d=unwrap((await request.get(base())).data);if(projectId.value===id){scripts.value=d.scripts;workers.value=d.workers;error.value='';}}catch(e){error.value=message(e);}finally{loading.value=false;}}
function resetEditor(){code.value=currentVersion.value?.code||'';parameters.value=JSON.stringify(currentVersion.value?.parameters||{},null,2);}
function selectVersion(){resetEditor();}
async function open(id:number){const project=projectId.value;try{const d=unwrap((await request.get(base()+id+'/')).data);if(projectId.value!==project)return;if(detail.value?.id!==id)advanced.value=false;detail.value=d;selectedVersion.value=d.versions?.[0]?.id;resetEditor();}catch(e){Message.error(message(e));}}
async function refreshDetail(){if(!detail.value)return;if(dirty.value){Message.info('请先保存或撤销修改。');return;}const selected=selectedVersion.value;await open(detail.value.id);if(advanced.value&&detail.value?.versions?.some(v=>v.id===selected)){selectedVersion.value=selected;resetEditor();}}
function closeDetail(){if(dirty.value){Modal.confirm({title:'放弃未保存的修改？',content:'关闭后将丢弃本次尚未保存的代码与参数修改。',onOk:()=>{detail.value=null;}});}else detail.value=null;}
async function save(){if(!detail.value)return;busy.value=true;try{await request.post(base()+detail.value.id+'/versions/',{code:code.value,parameters:JSON.parse(parameters.value),base_version:detail.value.latest?.number||0});await open(detail.value.id);await load();Message.success('已保存新版本，旧版本保留。');}catch(e){Message.error(message(e));}finally{busy.value=false;}}
async function regenerate(){if(!detail.value)return;if(dirty.value){Message.info('请先保存或撤销修改。');return;}busy.value=true;try{await request.post(base()+detail.value.id+'/regenerate/');await open(detail.value.id);await load();Message.success('已开始后台整理。');}catch(e){Message.error(message(e));}finally{busy.value=false;}}
async function remove(id:number){try{await request.delete(base()+id+'/');await load();}catch(e){Message.error(message(e));}}
async function download(format:'py'|'zip'='py'){if(!detail.value || !selectedVersion.value)return;try{const response=await request.get(base()+detail.value.id+'/download/',{params:{version:selectedVersion.value,download_format:format},responseType:'blob'});const url=URL.createObjectURL(response.data);const a=document.createElement('a');a.href=url;a.download=`case_${detail.value.testcase_id}_v${currentVersion.value?.number}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){Message.error(message(e));}}
async function prepareRun(id:number, versionId?:number){try{const s:Script=unwrap((await request.get(base()+id+'/')).data);runScript.value=s;runVersion.value=s.versions?.find(v=>v.id===versionId)||s.versions?.[0];if(!runVersion.value||runVersion.value.issues.length||(s.build&&!s.usable)||(runVersion.value.origin==='guided'&&!runVersion.value.verified)){Message.warning('请等待生成验证完成，或点击重新生成。');return;}const defaults={...runVersion.value.parameters};delete defaults.base_url;runParameters.value=JSON.stringify(defaults,null,2);workerId.value=workers.value[0]?.worker_id||'';headless.value=workers.value[0]?.supports_headed===false;runVisible.value=true;}catch(e){Message.error(message(e));}}
async function startRun(){if(!runScript.value||!runVersion.value)return false;busy.value=true;try{await request.post(base()+runScript.value.id+'/run/',{version:runVersion.value.id,worker_id:workerId.value,parameters:JSON.parse(runParameters.value),headless:headless.value,timeout_seconds:timeout.value});Message.success('已开始脚本执行，可在执行历史查看报告。');await load();if(detail.value&&!dirty.value)await open(detail.value.id);return true;}catch(e){Message.error(message(e));return false;}finally{busy.value=false;}}
async function stop(id:number){if(!detail.value)return;try{await request.post(base()+detail.value.id+'/stop/',{run_id:id});await refreshDetail();await load();Message.success('已请求停止。');}catch(e){Message.error(message(e));}}
function showReport(id:number|null){if(id){reportId.value=id;reportVisible.value=true;}}
let timer:ReturnType<typeof setInterval>|undefined;
watch(projectId,()=>{detail.value=null;runVisible.value=false;buildVisible.value=false;scripts.value=[];workers.value=[];void load();void openFromRoute();});
async function openFromRoute(){const id=Number(route.query.script);if(projectId.value&&Number.isInteger(id)&&id>0)await open(id);}
watch(()=>route.query.script,()=>void openFromRoute());
onMounted(()=>{void load();void openFromRoute();timer=setInterval(()=>{if(!document.hidden&&!loading.value){void load();if(detail.value&&!dirty.value&&!busy.value&&!runVisible.value)void refreshDetail();}},7000);});
onUnmounted(()=>clearInterval(timer));
</script>

<style scoped>
.scripts-page{height:100%;overflow:auto;padding:28px;box-sizing:border-box;background:var(--color-bg-1)}
.review-notes{margin-top:12px;line-height:1.7}
header{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:24px;flex-wrap:wrap}h1{margin:4px 0 10px;font-size:28px}header p{margin:0;color:var(--color-text-3)}.eyebrow{font-size:12px;letter-spacing:2px}.script-table{margin-top:20px}.muted{font-size:12px;color:var(--color-text-3);line-height:1.8;margin-top:5px}.toolbar{margin:16px 0}label{display:block;margin:14px 0 8px;font-weight:600}.code-editor :deep(textarea){font-family:Consolas,monospace;font-size:13px;line-height:1.65;tab-size:4}.basis{margin:16px 0}.basis p{line-height:1.8}
</style>
