<template>
  <a-modal :visible="visible" title="生成 Playwright 脚本" :width="620" :ok-loading="busy"
    ok-text="开始生成" @before-ok="submit" @cancel="emit('update:visible',false)">
    <p>{{ name }}</p>
    <a-alert style="margin:16px 0">平台将在后台生成脚本，再用独立浏览器复跑一次。执行完成且无错误即显示「复跑通过」，可以运行或下载。你可以关闭此窗口继续使用平台。</a-alert>
    <a-alert v-if="!loading&&!workers.length" type="warning">没有在线执行器，请启动项目执行器后刷新。</a-alert>
    <a-collapse><a-collapse-item header="高级设置（可选）">
    <a-radio-group v-model="live" type="button">
      <a-radio :value="true">生成并验证</a-radio>
      <a-radio v-if="!scriptId" :value="false">仅生成草稿</a-radio>
    </a-radio-group>
    <a-alert style="margin:16px 0">
      {{ live ? '使用项目网址实际操作，再独立复跑一次。默认自动选择在线执行器。' : '仅生成草稿不会验证可用性，仍需后续生成并验证。' }}
    </a-alert>
    <a-form v-if="live" layout="vertical">
      <a-form-item label="执行器">
        <a-select v-model="workerId" :loading="loading" placeholder="选择在线执行器">
          <a-option v-for="worker in workers" :key="worker.worker_id" :value="worker.worker_id">{{ worker.worker_id }}</a-option>
        </a-select>
      </a-form-item>
      <a-form-item label="每轮试跑超时（秒）"><a-input-number v-model="timeout" :min="30" :max="600" /></a-form-item>
      <a-checkbox v-model="headless" :disabled="selectedWorker?.supports_headed===false">无头模式（不显示浏览器）</a-checkbox>
      <p>使用项目配置的网址和独立浏览器环境，不继承个人浏览器登录状态。</p>
      <a-alert v-if="!loading&&!workers.length" type="warning">没有在线新版执行器，请启动项目执行器后刷新。</a-alert>
      <a-button size="small" style="margin-top:12px" @click="loadWorkers">刷新执行器</a-button>
    </a-form>
    </a-collapse-item></a-collapse>
  </a-modal>
</template>
<script setup lang="ts">
import {computed, ref, watch} from 'vue';
import {Message} from '@arco-design/web-vue';
import request from '@/utils/request';
const props=defineProps<{visible:boolean;projectId:number;name:string;caseId?:number;scriptId?:number}>();
const emit=defineEmits<{(e:'update:visible',v:boolean):void;(e:'submitted',id:number):void}>();
interface Worker {worker_id:string;runtime_version:number;supports_headed:boolean}
const workers=ref<Worker[]>([]),workerId=ref(''),live=ref(true),headless=ref(false),timeout=ref(180),busy=ref(false),loading=ref(false);
const selectedWorker=computed(()=>workers.value.find(w=>w.worker_id===workerId.value));
const base=()=>`/projects/${props.projectId}/playwright-scripts/`;
async function loadWorkers(){
  loading.value=true;
  try{const response=await request.get(base());const data=response.data?.data??response.data;workers.value=data.workers.filter((w:Worker)=>w.runtime_version>=9);if(!workers.value.some(w=>w.worker_id===workerId.value))workerId.value=workers.value[0]?.worker_id||'';}
  catch(e:any){Message.error(e?.error||e?.message||'读取执行器失败');}
  finally{loading.value=false;}
}
watch(selectedWorker,w=>{if(w?.supports_headed===false)headless.value=true;});
watch(()=>props.visible,v=>{if(v){live.value=true;void loadWorkers();}});
async function submit(){
  if(live.value&&!workerId.value){Message.warning('请选择在线执行器。');return false;}
  busy.value=true;
  try{
    const url=props.scriptId?base()+props.scriptId+'/build/':base();
    const response=await request.post(url,{testcase_id:props.caseId,validate_live:live.value,worker_id:workerId.value,headless:headless.value,timeout_seconds:timeout.value});
    const data=response.data?.data??response.data;
    emit('update:visible',false);emit('submitted',props.scriptId||data.id);
    Message.success(live.value?'已开始后台生成，完成后即可运行或下载。':'已提交草稿生成。');return true;
  }catch(e:any){Message.error(e?.response?.data?.message||e?.error||e?.message||'提交失败');return false;}
  finally{busy.value=false;}
}
</script>
