<template>
  <section v-if="groundedCases.length" class="grounding-panel" aria-label="用例依据与覆盖检查">
    <h3>依据与覆盖检查</h3>
    <p>原图、需求摘录与用例逐步关联。AI 评审通过表示有材料支持，实际执行结果请查看执行历史。</p>
    <a-alert v-if="pendingCount" type="warning">{{ pendingCount }} 条用例的预期或依据需要你判断。核对后可批量接受当前内容，补充说明选填；确认不会修改用例或代表执行通过。</a-alert>
    <a-alert v-else type="success">当前用例的依据检查已完成，仍需实际执行验证。</a-alert>
    <a-alert v-if="hasUnsavedChanges" type="info" class="unsaved-hint">请先保存或放弃草稿修改，再确认当前版本。</a-alert>
    <a-tabs v-model:active-key="section" class="sections">
      <a-tab-pane key="pending" :title="`待确认问题（${pendingCount}）`">
        <a-empty v-if="!pendingCount" description="当前没有阻断入库的依据问题。" />
        <div v-if="pendingCount && editable" class="batch-toolbar">
          <a-space wrap>
            <a-checkbox :model-value="allSelected" :indeterminate="!!checked.length&&!allSelected" :disabled="disabled" @change="toggleAll">全选 {{ pendingCount }} 条</a-checkbox>
            <a-button type="primary" :disabled="disabled||!checked.length" :loading="busy" @click="confirm(checked)">确认勾选项（{{ checked.length }}）</a-button>
            <a-button :disabled="disabled" @click="confirm(pending.map(c=>c.tc_sno))">确认全部 {{ pendingCount }} 条</a-button>
            <a-button v-if="checked.length" :disabled="busy" @click="checked=[]">取消选择</a-button>
          </a-space>
          <p>点击确认表示接受所选用例的当前内容。原问题会保留在确认记录中，之后可撤回；规则错误仍需修正。</p>
          <details class="optional-note"><summary>添加补充说明（选填，应用于本次确认的用例）</summary><a-textarea v-model="batchNote" :disabled="busy" placeholder="例如：已核对业务规则；或记录本次接受的假设。不填写也可确认。" :max-length="2000" :auto-size="{minRows:2,maxRows:5}" /><a-button v-if="batchNote" size="small" :disabled="busy" @click="batchNote=''">清空说明</a-button></details>
        </div>
        <article v-for="c in pending" :key="c.tc_sno" class="step pending-case">
          <a-space wrap><a-checkbox v-if="editable" :model-value="checked.includes(c.tc_sno)" :disabled="disabled" :aria-label="`选择 ${c.tc_name}`" @change="toggle(c.tc_sno)" /><strong>{{ c.tc_name }}</strong><a-tag color="orange">{{ c.confirmation_reasons?.length }} 个问题</a-tag></a-space>
          <details><summary>{{ c.confirmation_reasons?.[0] }}</summary><ul><li v-for="reason in c.confirmation_reasons" :key="reason">{{ reason }}</li></ul></details>
          <a-space wrap><a-button size="small" @click="selected=c.tc_sno;section='cases'">查看依据与步骤</a-button><a-button v-if="editable" size="small" :disabled="disabled" @click="confirm([c.tc_sno])">确认此条</a-button></a-space>
        </article>
      </a-tab-pane>
      <a-tab-pane key="confirmed" :title="`人工确认记录（${confirmed.length}）`">
        <a-empty v-if="!confirmed.length" description="暂无当前有效的人工确认记录。" />
        <article v-for="group in confirmationGroups" :key="group.id" class="step">
          <a-space wrap><strong>{{ group.review.username }} · {{ formatTime(group.review.at) }}</strong><a-tag>{{ group.cases.length }} 条</a-tag><a-button v-if="editable" size="small" :disabled="disabled" @click="$emit('revoke',group.cases.map(c=>c.tc_sno))">撤回本次确认（{{ group.cases.length }} 条）</a-button></a-space>
          <p>{{ group.review.note }}</p>
          <details><summary>查看本次用例与原问题</summary><div v-for="c in group.cases" :key="c.tc_sno" class="confirmation-case"><a-link @click="selected=c.tc_sno;section='cases'">{{ c.tc_name }}</a-link><ul><li v-for="reason in c.human_review?.accepted_reasons||[]" :key="reason">{{ reason }}</li></ul><a-button v-if="editable" size="small" :disabled="disabled" @click="$emit('revoke',[c.tc_sno])">撤回此条确认</a-button></div></details>
        </article>
      </a-tab-pane>
      <a-tab-pane v-if="coverage" key="coverage" title="覆盖检查">
        <h4>证据关联：{{ coverage.cited }} 已关联 · {{ coverage.pending }} 待确认 · {{ coverage.uncovered }} 未关联</h4>
        <div v-if="coverage.scope?.length"><strong>本次验收范围与排除项</strong><p v-for="item in coverage.scope" :key="item.id">{{ item.text }}</p></div>
        <p>共整理 {{ coverage.total }} 个证据项。这是材料与用例的引用关系，不等于功能覆盖率或执行成功率。</p>
        <a-table :data="coverage.items" :pagination="{pageSize:8}" row-key="id" :scroll="{x:680}">
          <template #columns>
            <a-table-column title="需求 / 可见事实"><template #cell="{record}"><strong>{{ record.text }}</strong><p>{{ record.document }} · {{ record.id }}</p><blockquote v-if="record.quote">{{ record.quote }}</blockquote></template></a-table-column>
            <a-table-column title="关联用例" :width="210"><template #cell="{record}"><a-link v-for="(id,i) in record.case_ids" :key="id" @click="$emit('open',id)">{{ record.case_names[i] }}</a-link><span v-if="!record.case_ids.length">暂未生成对应用例</span></template></a-table-column>
            <a-table-column title="状态" :width="95"><template #cell="{record}"><a-tag :color="record.status==='cited'?'green':'orange'">{{ record.status==='cited'?'已关联':record.status==='pending'?'待确认':'未关联' }}</a-tag></template></a-table-column>
          </template>
        </a-table>
        <a-alert v-if="coverage.review_gaps?.length" type="warning">AI 评审另标出 {{ coverage.review_gaps.length }} 个疑似遗漏项，请结合原文核实：<p v-for="item in coverage.review_gaps" :key="item.id">{{ item.id }} · {{ item.text }}</p></a-alert>
        <div v-for="unknown in coverage.unknowns" :key="unknown.unit_id"><strong>{{ unknown.document }} · 材料未明确</strong><ul><li v-for="q in unknown.questions" :key="q">{{ q }}</li></ul></div>
        <p v-if="coverage.duplicate_pair_count">发现 {{ coverage.duplicate_pair_count }} 组相似用例，仅提示，不自动删除：</p>
        <p v-for="(pair,i) in coverage.possible_duplicates" :key="i"><a-link v-for="(id,j) in pair.case_ids" :key="id" @click="$emit('open',id)">{{ pair.names[j] }} </a-link></p>
      </a-tab-pane>
      <a-tab-pane key="cases" :title="`逐条依据（${groundedCases.length}）`">
        <a-select v-model="selected" placeholder="选择用例查看依据" :options="groundedCases.map(c=>({value:c.tc_sno,label:`${c.confirmation_reasons?.length?'待确认 · ':''}${c.tc_name}`}))" allow-search />
        <article v-if="current" class="case-detail">
          <a-space wrap><strong>{{ current.tc_name }}</strong><a-button size="small" @click="$emit('open',current.tc_sno)">查看 / 编辑用例</a-button></a-space>
          <p>生成模型：{{ current.generation_model?.name || '—' }}；依据评审模型：{{ current.source_review?.model?.name || '—' }}</p>
          <ul v-if="current.confirmation_reasons?.length"><li v-for="reason in current.confirmation_reasons" :key="reason">{{ reason }}</li></ul>
          <div v-if="current.source_review?.non_blocking_questions?.length"><strong>背景未知（不阻断本条）</strong><p v-for="q in current.source_review.non_blocking_questions" :key="q.text">{{ q.text }}<br />评审理由：{{ q.reason }}</p></div>
          <div class="source-review-grid"><aside class="source-material"><strong>原图与需求摘录</strong><p>点击原图可放大核对。</p><div class="images"><figure v-for="img in images.filter(i=>current?.source_images?.includes(i.name))" :key="img.name"><a-image :src="img.url" width="100%" :alt="img.name" /><figcaption>{{ img.name }}</figcaption></figure></div><blockquote v-for="ref in documentEvidence" :key="ref.id"><strong>{{ ref.document }} · {{ ref.id }}</strong><p>{{ ref.quote||ref.text }}</p></blockquote></aside><div class="source-steps">
          <div v-for="(step,i) in current.steps" :key="i" class="step">
            <strong>步骤 {{ i+1 }} · {{ step.desc }}</strong>
            <p>预期：{{ step.expected }}</p>
            <a-tag :color="step.basis_type==='inference'?'orange':'blue'">{{ step.basis_type==='ui_fact'?'图片可见事实':step.basis_type==='document'?'文档明确要求':'推测 / 待确认' }}</a-tag>
            <blockquote v-for="ref in step.evidence_refs || []" :key="ref.id">{{ ref.document }} · {{ ref.id }}<br />{{ ref.text }}<br /><small v-if="ref.quote">原文 / 可见文案：{{ ref.quote }}</small></blockquote>
            <p v-if="step.uncertainty">{{ current.source_review?.non_blocking_questions?.some(q=>q.text===step.uncertainty)?'背景未知（评审已说明不阻断本条）':'待确认' }}：{{ step.uncertainty }}</p>
          </div>
          </div></div>
          <div v-if="current.human_review" class="confirmation-record"><p><strong>人工确认记录{{ current.human_review_current?'':'（内容已变更，需重新确认）' }}：</strong>{{ current.human_review.username }} · {{ formatTime(current.human_review.at) }}<br />{{ current.human_review.note }}</p><a-button v-if="editable" size="small" :disabled="disabled" @click="$emit('revoke',[current.tc_sno])">撤回此条确认</a-button></div>
          <details v-if="current.human_reviews?.length"><summary>历史确认与撤回记录（{{ current.human_reviews.length }}）</summary><p v-for="(record,index) in current.human_reviews" :key="index">{{ record.username }} · {{ formatTime(record.at) }}：{{ record.note }}<br /><span v-if="record.revoked_at">由 {{ record.revoked_by_username }} 于 {{ formatTime(record.revoked_at) }} 撤回</span><span v-else>已被后续确认替代</span></p></details>
          <template v-if="current.confirmation_reasons?.length && editable">
            <a-textarea v-model="notes[current.tc_sno]" :disabled="busy" placeholder="补充说明（选填）：可记录核对依据或接受的假设，无须重复填写。" :max-length="2000" :auto-size="{minRows:2,maxRows:5}" />
            <a-button class="confirm" type="primary" :loading="busy" :disabled="disabled" @click="confirm([current.tc_sno],notes[current.tc_sno]||'')">确认接受当前用例</a-button>
          </template>
        </article>
      </a-tab-pane>
    </a-tabs>
  </section>
</template>

<script lang="ts">
export interface Evidence {id:string;document:string;text:string;quote?:string;status?:string;case_ids:string[];case_names:string[]}
export interface Coverage {scope?:Evidence[];review_gaps?:Evidence[];total:number;cited:number;pending:number;uncovered:number;items:Evidence[];unknowns:{unit_id:string;document:string;questions:string[]}[];possible_duplicates:{case_ids:string[];names:string[]}[];duplicate_pair_count:number}
export interface GroundStep {desc:string;expected:string;basis_type?:string;evidence_refs?:Evidence[];uncertainty?:string}
export interface HumanReview {username:string;at:string;note:string;batch_id?:string;accepted_reasons?:string[];revoked_at?:string;revoked_by_username?:string}
export interface GroundCase {tc_sno:string;tc_name:string;grounding_version?:number;confirmation_reasons?:string[];review_fingerprint?:string;human_review_current?:boolean;source_images?:string[];steps:GroundStep[];generation_model?:{name:string};source_review?:{non_blocking_questions?:{text:string;reason:string}[];model?:{name:string}};human_review?:HumanReview;human_reviews?:HumanReview[]}
</script>
<script setup lang="ts">
import {computed,ref,watch} from 'vue'
const props=defineProps<{cases:GroundCase[];coverage?:Coverage;images:{name:string;url:string}[];editable:boolean;busy:boolean;hasUnsavedChanges?:boolean}>()
const emit=defineEmits<{open:[id:string];confirm:[ids:string[],note:string];revoke:[ids:string[]]}>()
const selected=ref(''),notes=ref<Record<string,string>>({})
const checked=ref<string[]>([]),batchNote=ref('')
const section=ref('cases')
const groundedCases=computed(()=>props.cases.filter(c=>c.grounding_version))
const pending=computed(()=>groundedCases.value.filter(c=>c.confirmation_reasons?.length))
const pendingCount=computed(()=>pending.value.length)
const allSelected=computed(()=>!!pendingCount.value&&checked.value.length===pendingCount.value)
const disabled=computed(()=>props.busy||!!props.hasUnsavedChanges||!props.editable)
const confirmed=computed(()=>groundedCases.value.filter(c=>c.human_review&&c.human_review_current))
const confirmationGroups=computed(()=>{
  const groups=new Map<string,{id:string;review:HumanReview;cases:GroundCase[]}>()
  for(const c of confirmed.value){const review=c.human_review!,id=review.batch_id||c.tc_sno;const group=groups.get(id)||{id,review,cases:[]};group.cases.push(c);groups.set(id,group)}
  return [...groups.values()].sort((a,b)=>b.review.at.localeCompare(a.review.at))
})
function toggle(id:string){if(disabled.value)return;checked.value=checked.value.includes(id)?checked.value.filter(i=>i!==id):[...checked.value,id]}
function toggleAll(){if(!disabled.value)checked.value=allSelected.value?[]:pending.value.map(c=>c.tc_sno)}
function confirm(ids:string[],note=batchNote.value){if(!disabled.value&&ids.length)emit('confirm',[...ids],note)}
function formatTime(value:string){return new Date(value).toLocaleString('zh-CN',{hour12:false})}
const current=computed(()=>groundedCases.value.find(c=>c.tc_sno===selected.value))
const documentEvidence=computed(()=>Array.from(new Map((current.value?.steps.flatMap(s=>s.evidence_refs||[])||[]).filter(e=>!props.images.some(i=>i.name===e.document)).map(e=>[e.id,e])).values()))
defineExpose({show:(value:string)=>{section.value=value},confirmationSaved:(ids:string[])=>{checked.value=[];batchNote.value='';ids.forEach(id=>delete notes.value[id])}})
watch(pending,list=>{const ids=new Set(list.map(c=>c.tc_sno));checked.value=checked.value.filter(id=>ids.has(id))})
watch(groundedCases, list=>{if(!list.some(c=>c.tc_sno===selected.value))selected.value=list.find(c=>c.confirmation_reasons?.length)?.tc_sno||list[0]?.tc_sno||''},{immediate:true})
</script>
<style scoped>
.grounding-panel{border:1px solid var(--sgui-line);border-radius:10px;padding:18px;margin:20px 0;background:var(--sgui-soft);overflow-wrap:anywhere}
h3{margin:0 0 8px}p,li,blockquote{font-size:12px;line-height:1.8}p{color:var(--sgui-muted)}
.sections,.case-detail,.confirm{margin-top:16px}.step{padding:16px 0;border-bottom:1px solid var(--sgui-line)}
.unsaved-hint{margin-top:12px}.batch-toolbar{padding:16px;border:1px solid var(--sgui-line);border-radius:10px;background:var(--sgui-surface);margin:12px 0}.batch-toolbar p{margin:10px 0}.pending-case details{margin:10px 0}summary{cursor:pointer;font-size:13px;line-height:1.7;color:var(--sgui-muted)}.optional-note textarea{margin-top:10px}.confirmation-case{padding:12px 0;border-bottom:1px solid var(--sgui-line)}.confirmation-record{margin:16px 0}
blockquote{margin:10px 0;padding:8px 12px;border-left:3px solid var(--sgui-blue);background:var(--sgui-surface)}
.source-review-grid{display:grid;grid-template-columns:minmax(240px,.9fr) minmax(0,1.3fr);gap:24px;margin-top:20px}.source-material{padding:14px;background:var(--sgui-surface);border:1px solid var(--sgui-line);border-radius:8px;align-self:start}.source-steps{min-width:0}.images{display:flex;gap:12px;flex-wrap:wrap}figure{margin:12px 0;width:100%}figcaption{font-size:11px;margin-top:6px}.arco-link{display:block}@media(max-width:1050px){.source-review-grid{grid-template-columns:1fr}}
</style>
