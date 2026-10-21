<template>
  <a-modal :visible="visible" title="选择需求文档" width="min(720px, 94vw)" :mask-closable="false" @cancel="close">
    <div class="document-picker">
      <p>可混合选择新文档和当前项目已有文档。确定后应用，取消不会改变原来的材料。</p>
      <a-tabs v-model:active-key="section">
        <a-tab-pane key="upload" title="上传新文档">
          <div class="upload-area" @dragover.prevent @drop.prevent="addFiles(Array.from($event.dataTransfer?.files||[]))">
            <input ref="fileInput" type="file" multiple accept=".txt,.md,.pdf,.docx" aria-label="选择本地需求文件" class="hidden-picker" @change="pickFiles" />
            <a-button @click="fileInput?.click()">选择本地文件</a-button>
            <p>或将文件拖到这里。支持 TXT / MD / PDF / DOCX，单份最多 {{ maxFileBytes / MB }} MB。</p>
          </div>
        </a-tab-pane>
        <a-tab-pane key="existing" title="选用已有文档">
          <a-input v-model="search" placeholder="搜索当前项目的文档名称或编号" allow-clear aria-label="搜索已有需求文档" />
          <div class="existing-documents" aria-label="当前项目已有需求文档">
            <a-empty v-if="!filteredDocuments.length" :description="documents.length?'没有匹配的文档':'当前项目暂无已有需求文档，可上传新文档。'" />
            <div v-for="doc in filteredDocuments" :key="doc.id" class="document-option">
              <a-checkbox :model-value="draftIds.includes(doc.id)" :disabled="!draftIds.includes(doc.id)&&selectedCount>=maxCount" :aria-label="`选用 ${doc.title} ${doc.id.slice(0,8)}`" @change="toggleDocument(doc.id)"><strong>{{ doc.title }}</strong><small>{{ doc.word_count||0 }} 字 · {{ formatDate(doc.uploaded_at) }} · {{ doc.id.slice(0,8) }}</small></a-checkbox>
            </div>
          </div>
        </a-tab-pane>
      </a-tabs>
      <a-alert v-if="error" type="warning" closable @close="error=''">{{ error }}</a-alert>
      <div class="selection-heading"><strong>已选 {{ selectedCount }}/{{ maxCount }} 份</strong><a-button v-if="selectedCount" type="text" @click="draftFiles=[];draftIds=[];error=''">清空本次选择</a-button></div>
      <ul class="selected-documents" aria-label="弹窗已选文档">
        <li v-for="(file,index) in draftFiles" :key="fileKey(file)"><div><strong>{{ file.name }}</strong><small>新文档 · {{ formatSize(file.size) }} · 生成时上传</small></div><a-button type="text" :aria-label="`移除新文档 ${file.name}`" @click="draftFiles.splice(index,1)">移除</a-button></li>
        <li v-for="id in draftIds" :key="id"><div><strong>{{ documents.find(d=>d.id===id)?.title||id }}</strong><small>已有文档 · {{ id.slice(0,8) }}</small></div><a-button type="text" :aria-label="`移除已有文档 ${id.slice(0,8)}`" @click="toggleDocument(id)">移除</a-button></li>
      </ul>
    </div>
    <template #footer><a-button @click="close">取消</a-button><a-button type="primary" @click="apply">确定选择（{{ selectedCount }} 份）</a-button></template>
  </a-modal>
</template>

<script lang="ts">
export interface ExistingDocument {id:string;title:string;word_count?:number;uploaded_at?:string}
</script>
<script setup lang="ts">
import {computed,ref,watch} from 'vue'
const props=defineProps<{visible:boolean;files:File[];ids:string[];documents:ExistingDocument[];maxCount:number;maxFileBytes:number;maxUploadBytes:number;otherUploadBytes:number}>()
const emit=defineEmits<{'update:visible':[value:boolean];apply:[files:File[],ids:string[]]}>()
const draftFiles=ref<File[]>([]),draftIds=ref<string[]>([]),search=ref(''),error=ref(''),section=ref('upload'),fileInput=ref<HTMLInputElement>()
const MB=1024*1024
const selectedCount=computed(()=>draftFiles.value.length+draftIds.value.length)
const uploadBytes=computed(()=>props.otherUploadBytes+draftFiles.value.reduce((sum,f)=>sum+f.size,0))
const filteredDocuments=computed(()=>props.documents.filter(d=>`${d.title} ${d.id}`.toLowerCase().includes(search.value.trim().toLowerCase())))
const fileKey=(file:File)=>`${file.name}:${file.size}:${file.lastModified}`
const formatSize=(bytes:number)=>bytes>=MB?`${(bytes/MB).toFixed(1)} MB`:`${Math.ceil(bytes/1024)} KB`
const formatDate=(value?:string)=>value?new Date(value).toLocaleDateString('zh-CN'):'已有材料'
watch(()=>props.visible,visible=>{if(visible){draftFiles.value=[...props.files];draftIds.value=[...props.ids];search.value='';error.value='';section.value='upload'}})
function toggleDocument(id:string){if(draftIds.value.includes(id))draftIds.value=draftIds.value.filter(value=>value!==id);else if(selectedCount.value<props.maxCount)draftIds.value.push(id)}
function addFiles(files:File[]){
  const problems:string[]=[]
  for(const file of files){
    if(draftFiles.value.some(f=>fileKey(f)===fileKey(file))){problems.push(`${file.name}：已选择，已跳过重复文件`);continue}
    if(!/\.(txt|md|pdf|docx)$/i.test(file.name)){problems.push(`${file.name}：不支持此格式`);continue}
    if(!file.size||file.size>props.maxFileBytes){problems.push(`${file.name}：文件不能为空，单份最多 ${props.maxFileBytes/MB} MB`);continue}
    if(selectedCount.value>=props.maxCount){problems.push(`新文档与已有文档合计最多 ${props.maxCount} 份`);continue}
    if(uploadBytes.value+file.size>props.maxUploadBytes){problems.push(`待上传文档与图片合计最多 ${props.maxUploadBytes/MB} MB`);continue}
    draftFiles.value.push(file)
  }
  error.value=[...new Set(problems)].join('；')
}
function pickFiles(event:Event){const input=event.target as HTMLInputElement;addFiles(Array.from(input.files||[]));input.value=''}
function close(){emit('update:visible',false)}
function apply(){if(selectedCount.value>props.maxCount||uploadBytes.value>props.maxUploadBytes){error.value='所选材料超过数量或大小限制，请移除部分文件。';return}emit('apply',[...draftFiles.value],[...draftIds.value]);close()}
</script>
<style scoped>
.document-picker{max-height:65vh;overflow:auto;padding-right:6px;color:var(--sgui-text)}p,small{color:var(--sgui-muted);font-size:12px;line-height:1.8}.hidden-picker{display:none}.upload-area{padding:24px;border:1px dashed var(--sgui-line);border-radius:10px;text-align:center;background:var(--sgui-soft)}.existing-documents{max-height:260px;overflow:auto;margin-top:12px}.document-option{padding:12px 6px;border-bottom:1px solid var(--sgui-line);overflow-wrap:anywhere}.document-option :deep(.arco-checkbox){align-items:flex-start;width:100%}.document-option :deep(.arco-checkbox-label){min-width:0}.selected-documents li>div{min-width:0;overflow-wrap:anywhere}strong{font-size:13px}small{display:block}.selection-heading{display:flex;align-items:center;justify-content:space-between;margin-top:14px}.selected-documents{list-style:none;padding:0;margin:8px 0;max-height:210px;overflow:auto}.selected-documents li{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 0;border-bottom:1px solid var(--sgui-line)}.selected-documents button{flex:none}
</style>
