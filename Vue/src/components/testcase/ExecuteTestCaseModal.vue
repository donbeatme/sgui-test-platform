<template>
  <a-modal
    v-model:visible="internalVisible"
    title="执行测试用例"
    width="min(640px, calc(100vw - 32px))"
    :ok-button-props="{disabled:checking || !preflight?.ready}"
    @ok="handleConfirm"
    @cancel="handleCancel"
    ok-text="开始执行"
    cancel-text="取消"
  >
    <div class="execute-modal-content">
      <a-descriptions :column="1" bordered size="small" class="testcase-info">
        <a-descriptions-item label="用例名称">
          {{ testCase?.name || '-' }}
        </a-descriptions-item>
        <a-descriptions-item label="用例等级">
          <a-tag :color="getLevelColor(testCase?.level)">
            {{ testCase?.level || '-' }}
          </a-tag>
        </a-descriptions-item>
      </a-descriptions>

      <a-divider orientation="left">执行前检查</a-divider>
      <a-spin v-if="checking" />
      <a-alert v-if="checkError" type="error">{{ checkError }}</a-alert>
      <template v-if="preflight">
        <a-alert :type="preflight.ready?'success':'error'">{{ preflight.ready?'配置检查通过，可以开始执行。':'有必须处理的问题，解决后请重新检查。' }}</a-alert>
        <div v-if="preflight.errors.length" class="check-section">
          <strong>必须处理</strong>
          <p v-for="item in preflight.errors" :key="item.code" class="check-error">{{ item.message }}</p>
          <a-button v-if="preflight.errors.some(item => ['missing_url','ambiguous_url'].includes(item.code))" size="small" @click="openProject">配置项目地址</a-button>
          <p v-if="preflight.errors.some(item => item.code==='missing_url')">在项目管理中编辑当前项目的系统凭据，填写一个网站地址。该项目下未指定网址的用例会统一使用它，账号和密码可以留空。</p>
        </div>
        <div v-if="preflight.warnings.length" class="check-section check-advisory">
          <strong>建议核查 · 不阻止开始执行</strong>
          <p v-for="item in preflight.warnings" :key="item.code">{{ item.message }}</p>
        </div>
        <p v-if="preflight.target_urls.length">入口（{{ preflight.url_source==='project'?'沿用项目配置':'用例指定' }}）：{{ preflight.target_urls.join('；') }}</p>
        <small>{{ preflight.scope }}</small>
      </template>
      <a-space wrap style="margin-top:12px"><a-button size="small" :loading="checking" @click="checkReadiness">重新检查</a-button><a-button v-if="preflight?.source_job_id" size="small" @click="openSource">查看来源与确认依据</a-button></a-space>
      <a-divider orientation="left">执行选项</a-divider>

      <a-alert style="margin-bottom: 16px" type="info">
        执行进度在「LLM 对话」中查看，完成后可在「执行历史」查看本次报告，首页执行统计同步更新。浏览器工具使用本机可见模式时，将打开独立浏览器窗口展示 AI 操作；每步截图保存在用例详情中。执行期间请勿操作测试窗口。
      </a-alert>

      <a-form :model="formData" layout="vertical">
        <a-form-item>
          <a-checkbox v-model="formData.generatePlaywrightScript">
            <span class="checkbox-label">
              <span class="checkbox-text">
                <span class="checkbox-title">保存本次执行的 Playwright 脚本</span>
                <span class="checkbox-desc">执行结束后在后台整理代码，可在「Playwright 脚本」中查看和直接复跑。保存不会再次操作网站；首次复跑通过后标记为已验证。</span>
              </span>
            </span>
          </a-checkbox>
        </a-form-item>
      </a-form>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue';
import request from '@/utils/request';
import { useRouter } from 'vue-router';
import type { TestCase } from '@/services/testcaseService';

interface Props {
  visible: boolean;
  testCase: TestCase | null;
}

interface Emits {
  (e: 'update:visible', value: boolean): void;
  (e: 'confirm', options: { generatePlaywrightScript: boolean }): void;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();
const router = useRouter();
interface Preflight {ready:boolean;errors:{code:string;message:string}[];warnings:{code:string;message:string}[];target_urls:string[];url_source:string;scope:string;source_job_id?:string}
const preflight=ref<Preflight>(),checking=ref(false),checkError=ref('');
let checkVersion=0;
async function checkReadiness(){
  const value=props.testCase,version=++checkVersion;
  preflight.value=undefined;checkError.value='';
  if(!value)return;
  checking.value=true;
  try{const response=await request.get(`/projects/${value.project}/testcases/${value.id}/execution-preflight/`);if(version===checkVersion)preflight.value=response.data.data as Preflight}
  catch(e){if(version===checkVersion)checkError.value=(e as {error?:string}).error||'检查失败，请确认服务可用后重试。'}
  finally{if(version===checkVersion)checking.value=false}
}
function openSource(){if(preflight.value?.source_job_id){internalVisible.value=false;void router.push({path:'/workbench',query:{job:preflight.value.source_job_id}})}}
function openProject(){internalVisible.value=false;void router.push('/projects')}

const internalVisible = computed({
  get: () => props.visible,
  set: (val) => emit('update:visible', val),
});

const formData = ref({
  generatePlaywrightScript: false,
});

// 重置表单
watch(
  () => props.visible,
  (val) => {
    if (val) {
      void checkReadiness();
      formData.value = {
        generatePlaywrightScript: false,
      };
    }
  }
);

const getLevelColor = (level?: string) => {
  const colors: Record<string, string> = {
    P0: 'red',
    P1: 'orange',
    P2: 'blue',
    P3: 'gray',
  };
  return colors[level || ''] || 'gray';
};

const handleConfirm = () => {
  if(checking.value || !preflight.value?.ready)return;
  emit('confirm', {
    generatePlaywrightScript: formData.value.generatePlaywrightScript,
  });
  internalVisible.value = false;
};

const handleCancel = () => {
  internalVisible.value = false;
};
</script>

<style scoped lang="less">
.execute-modal-content {
  p {font-size:12px;line-height:1.8;overflow-wrap:anywhere;}
  .check-error {color:var(--color-danger-6)}
  .check-section {margin:12px 0;padding:12px;border:1px solid var(--color-border-2);border-radius:8px;}
  .check-advisory {background:var(--color-fill-1);color:var(--color-text-2);}
  small {color:var(--color-text-3)}
  .testcase-info {
    margin-bottom: 16px;
  }

  .checkbox-label {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    font-weight: 500;
  }


  .checkbox-text {
    display: flex;
    flex-direction: column;
  }

  .checkbox-title {
    font-weight: 500;
  }

  .checkbox-desc {
    font-size: 12px;
    color: var(--color-text-3);
    margin-top: 4px;
    font-weight: 400;
  }
}
</style>
