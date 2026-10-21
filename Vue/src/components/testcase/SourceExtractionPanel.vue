<template>
  <section v-if="progress?.total" class="source-extraction" aria-label="文档依据整理进度">
    <p aria-live="polite">文档依据已整理 {{ progress.completed }}/{{ progress.total }} 个材料单元<span v-if="progress.repairing"> · {{ progress.repairing }} 个正在自动修复引用</span>。已通过的结果会保留。</p>
    <template v-if="progress.warning_count">
      <a-alert type="warning">{{ progress.warning_count }} 个材料单元仍有原文引用待核对，其他材料继续生成。未核实的引用没有进入已验证依据；请在评审用例时对照原材料检查。</a-alert>
      <a-collapse class="details">
        <a-collapse-item key="quotes" :header="`原文引用待核对（${progress.warning_count} 个材料单元）`">
          <p>以下内容经过两次自动修复仍未通过。原文节选用于定位，不代表支持模型提出的要求；完整文档和已通过的事实均已保留。</p>
          <a-table :data="issues" :pagination="{pageSize:5,showTotal:true}" row-key="key" :scroll="{x:640}">
            <template #columns>
              <a-table-column title="来源" :width="180"><template #cell="{record}">{{ record.document }}<p>{{ record.unit_id }}<span v-if="record.pages?.length"> · 第 {{ record.pages.join('、') }} 页</span></p><small v-if="record.fact_index">第 {{ record.fact_index }} 条提取项</small></template></a-table-column>
              <a-table-column title="待核对内容"><template #cell="{record}"><strong>{{ record.text || '材料提取结果需要核对' }}</strong><p>{{ record.reason }}</p><blockquote v-if="record.quote">模型引用（未通过）：{{ record.quote }}</blockquote><details v-if="record.source_excerpt"><summary>查看原文附近内容</summary><pre>{{ record.source_excerpt }}</pre></details></template></a-table-column>
            </template>
          </a-table>
        </a-collapse-item>
      </a-collapse>
    </template>
  </section>
</template>

<script lang="ts">
export interface SourceIssue {unit_id:string;document:string;pages:number[];fact_index?:number;reason:string;text:string;quote:string;source_excerpt:string}
export interface SourceExtraction {total:number;completed:number;repairing:number;warning_count:number;warnings:{unit_id:string;document:string;pages:number[];attempts:number;issues:SourceIssue[]}[]}
</script>
<script setup lang="ts">
import {computed} from 'vue'
const props=defineProps<{progress?:SourceExtraction}>()
const issues=computed(()=>props.progress?.warnings.flatMap(unit=>unit.issues.map((item,index)=>({...item,key:`${unit.unit_id}-${index}`})))||[])
</script>

<style scoped>
.source-extraction{margin:16px 0;overflow-wrap:anywhere}.details{margin-top:12px}p,small{color:var(--sgui-muted);font-size:12px;line-height:1.8}blockquote{margin:10px 0;padding:8px 12px;border-left:3px solid var(--sgui-blue);background:var(--sgui-soft);font-size:12px}summary{cursor:pointer;color:var(--sgui-blue)}pre{white-space:pre-wrap;font:inherit;line-height:1.8}
</style>
