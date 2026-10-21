<template>
  <section class="control-panel" aria-label="图片识别与控件核对">
    <div class="panel-heading"><div><h3>图片识别与控件核对</h3><p>先确认 AI 看到了什么，再让它生成用例。标注框仅表示截图中的位置。</p></div><a-tag :color="changed?'orange':'blue'">{{ changed?'有未保存修改':`${pages.length} 张图片` }}</a-tag></div>
    <a-alert v-if="editable" type="info">逐张核对名称、文案与可见状态；可补充遗漏、排除误识别，并在原图上重新框选位置。核对控件不代表确认隐藏业务规则。</a-alert>
    <a-empty v-if="!pages.length" description="图片解析完成后，这里会显示控件清单。" />
    <a-modal v-model:visible="previewVisible" title="原图与识别框 · 放大核对" fullscreen :body-style="{maxHeight:'none',height:'calc(100vh - 64px)',overflow:'auto',boxSizing:'border-box'}" :footer="false" :mask-closable="false" @cancel="finishPreview"><div ref="previewHost"></div></a-modal>
    <Teleport :to="previewHost || 'body'" :disabled="!previewVisible || !previewHost">
    <div v-if="page" class="control-workspace" :class="{expanded:previewVisible}">
      <div v-if="previewVisible" class="expanded-actions"><span>{{ editable?'修改暂存在本页，保存核对进度后才会提交。':'查看原图标注、控件详情与关联用例。' }}</span><a-space wrap><a-button v-if="editable" :disabled="busy" @click="cancelPreview">取消本次放大修改</a-button><a-button :disabled="busy" type="primary" @click="finishPreview">返回工作台</a-button></a-space></div>
      <div v-if="editable" class="history-tools"><a-space wrap><a-button :disabled="busy||!history.canUndo.value" @click="undo">撤销</a-button><a-button :disabled="busy||!history.canRedo.value" @click="redo">重做</a-button><a-popconfirm content="放弃未保存的控件修改，恢复上次保存状态？" @ok="discard"><a-button :disabled="busy||!changed">放弃未保存修改</a-button></a-popconfirm></a-space><span>{{ changed?'有未保存修改':'与已保存内容一致' }}</span></div>
      <div class="page-picker"><a-select v-model="pageId" :disabled="busy" aria-label="选择核对图片" :options="pages.map(p=>({value:p.unit_id,label:`${p.reviewed?'✓ ':''}${p.image_name} · ${p.controls.filter(c=>!c.excluded).length} 个控件`}))" /><a-button v-if="editable" :disabled="busy" @click="nextPage">下一张未核对</a-button></div>
      <a-alert v-if="page.legacy" type="warning">此旧任务只保留文字识别结果，没有结构化控件位置。下方可查看原有引用；新建图文任务即可使用生成前核对与标注。</a-alert>
      <div class="review-layout">
        <div class="image-side">
          <div class="image-tools"><strong>{{ page.image_name }}</strong><a-space wrap><a-button v-if="!previewVisible" size="small" @click="openPreview">放大原图</a-button><a-switch v-model="showBoxes" checked-text="显示标注" unchecked-text="隐藏标注" aria-label="显示图片标注" /></a-space></div>
          <div v-if="previewVisible" class="zoom-tools"><a-button :disabled="zoom<=50" aria-label="缩小图片" @click="zoom=Math.max(50,zoom-25)">−</a-button><span>{{ zoom }}%</span><a-button :disabled="zoom>=300" aria-label="放大图片" @click="zoom=Math.min(300,zoom+25)">＋</a-button><a-button @click="zoom=100">适应宽度</a-button></div>
          <div class="image-viewport" :class="{'zoomed-viewport':previewVisible}">
          <div v-if="imageUrl" class="image-board" role="group" aria-label="原图标注区域" :style="{width:`${previewVisible?zoom:100}%`}" :class="{drawing}" @pointerdown="beginDraw" @pointermove="moveDraw" @pointerup="endDraw" @pointercancel="cancelDraw">
            <img :src="imageUrl" :alt="page.image_name" draggable="false" />
            <template v-if="showBoxes"><button v-for="(c,index) in page.controls" v-show="c.bbox&&!c.excluded" :key="c.id||index" type="button" class="control-box" :class="{selected:index===selectedIndex}" :style="boxStyle(c.bbox)" :aria-label="`定位控件 ${index+1} ${c.name}`" @click="selectControl(index)"><span>{{ index+1 }}</span></button></template>
            <div v-if="draftBox" class="draw-box" :style="boxStyle(draftBox)"></div>
          </div>
          </div>
          <p v-if="drawing" class="draw-hint" role="status">拖动框住「{{ current?.name }}」；点击“取消框选”或按 Esc 可取消。</p>
          <p>标注可能有偏差，不能当作网页定位器。{{ page.controls.filter(c=>!c.excluded&&!c.bbox).length }} 个有效控件尚无位置。</p>
          <a-space v-if="editable&&current" wrap><a-button :type="drawing?'primary':'secondary'" :disabled="busy||current.excluded" @click="toggleDraw">{{ drawing?'取消框选':'为选中控件框选位置' }}</a-button><a-button :disabled="busy||!current.bbox" @click="updateControl({bbox:null})">清除选中标注</a-button></a-space>
          <p v-if="page.review">最近核对：{{ page.review.username }} · {{ new Date(page.review.at).toLocaleString() }} · {{ page.edit_count }} 次内容修订</p>
        </div>
        <div class="control-side">
          <div class="control-tools"><strong>控件清单 · {{ page.controls.filter(c=>!c.excluded).length }} 个有效</strong><a-button v-if="editable" :disabled="busy||page.controls.length>=60" @click="addControl">补充控件</a-button></div>
          <a-checkbox v-if="page.controls.some(c=>c.excluded)" v-model="showExcluded">显示已排除 / 删除的控件</a-checkbox>
          <div class="control-list" aria-label="识别控件清单"><button v-for="(c,index) in page.controls" v-show="showExcluded||!c.excluded" :key="c.id||index" type="button" :class="{active:selectedIndex===index,excluded:c.excluded}" @click="selectControl(index)"><b>{{ index+1 }}</b><span><strong>{{ c.name||'未命名控件' }}</strong><small>{{ c.origin==='human'?'人工修订':c.origin==='legacy'?'旧版分析':'AI 原始识别' }} · {{ typeLabel(c.type) }} · {{ c.excluded?'已排除 / 删除':c.bbox?'已有标注':'未标注' }} · {{ c.case_ids?.length||0 }} 条关联用例</small></span></button></div>
          <a-empty v-if="!page.controls.length" description="没有识别到控件，可查看原图后手工补充。" />
          <a-form v-if="current" layout="vertical" class="control-editor" :disabled="!editable||busy" @focusout="history.endGroup">
            <div class="field-row"><a-form-item label="控件名称"><a-input :model-value="current.name" aria-label="控件名称" :max-length="120" @update:model-value="updateControl({name:$event},'name')" /></a-form-item><a-form-item label="控件类型"><a-select :model-value="current.type" aria-label="控件类型" :options="typeOptions" @update:model-value="updateControl({type:String($event)})" /></a-form-item></div>
            <a-form-item label="可见文案 / 占位符"><a-input :model-value="current.text" aria-label="可见文案或占位符" :max-length="600" @update:model-value="updateControl({text:$event},'text')" /></a-form-item>
            <a-form-item label="可见状态"><a-textarea :model-value="current.state" aria-label="控件可见状态" :max-length="300" placeholder="仅描述看得见的状态，不确定写未知" @update:model-value="updateControl({state:$event},'state')" /></a-form-item>
            <a-checkbox v-if="editable" :model-value="current.excluded" :disabled="busy" @update:model-value="updateControl({excluded:!!$event})">排除此控件（误识别或不在本次范围）</a-checkbox>
          </a-form>
          <a-space v-if="editable&&current" wrap><a-button v-if="current.original" :disabled="busy" @click="restoreOriginal">恢复模型原值</a-button><a-button v-if="current.excluded" :disabled="busy" @click="updateControl({excluded:false})">恢复控件</a-button><a-button v-else status="danger" :disabled="busy" @click="deleteControl">删除控件</a-button></a-space>
          <p v-if="editable">已有控件删除后不参与生成，可再次恢复；核对记录保留。撤销 / 重做仅针对未保存修改。</p>
          <template v-if="current"><p>来源：{{ current.origin==='human'?'人工补充 / 修正':current.origin==='legacy'?'旧版文字分析':'模型识别' }}</p><details v-if="current.original"><summary>查看模型识别原值</summary><p>{{ current.original.name }} · {{ typeLabel(current.original.type) }}<br />文案：{{ current.original.text||'空' }}<br />状态：{{ current.original.state||'未知' }}</p></details><div class="linked-cases"><strong>关联用例</strong><p v-if="!current.case_ids?.length">{{ editable?'继续生成后显示关联；排除控件不会作为有效依据。':'暂无用例显式引用此控件。' }}</p><a-link v-for="(id,index) in current.case_ids" :key="id" @click="finishPreview(); $emit('open',id)">{{ current.case_names?.[index]||id }}</a-link></div></template>
        </div>
      </div>
      <div v-if="editable" class="review-footer"><a-checkbox :model-value="page.reviewed" :disabled="busy" @update:model-value="setReviewed(!!$event)">已核对本图的控件与标注（允许无控件）</a-checkbox><p>已核对 {{ pages.filter(p=>p.reviewed).length }}/{{ pages.length }} 张。内容修改后，本图需要重新勾选。</p><a-space wrap><a-button :loading="busy" @click="submit(false)">保存核对进度</a-button><a-button type="primary" :loading="busy" :disabled="!pages.length||pages.some(p=>!p.reviewed)" @click="submit(true)">按核对结果继续生成</a-button></a-space></div>
    </div>
    </Teleport>
  </section>
</template>

<script lang="ts">
export interface VisualControl {id:string;name:string;type:string;text:string;state:string;bbox:number[]|null;excluded:boolean;origin?:string;original?:VisualControl|null;case_ids?:string[];case_names?:string[]}
export interface ControlImage {unit_id:string;image_name:string;page:string;legacy:boolean;controls:VisualControl[];review?:{username:string;at:string}|null;edit_count:number}
export interface ControlPayload {unit_id:string;controls:VisualControl[];reviewed:boolean}
</script>
<script setup lang="ts">
import {computed,ref,watch,onMounted,onUnmounted} from 'vue'
import {useLocalHistory} from '@/composables/useLocalHistory'
const props=defineProps<{items:ControlImage[];images:{name:string;url:string}[];editable:boolean;busy:boolean}>()
const emit=defineEmits<{save:[images:ControlPayload[],proceed:boolean];open:[id:string];dirty:[value:boolean]}>()
const pages=ref<(ControlImage&{reviewed:boolean})[]>([]),pageId=ref(''),selectedIndex=ref(0),showBoxes=ref(true),drawing=ref(false),start=ref<number[]>(),draftBox=ref<number[]>()
const previewVisible=ref(false),showExcluded=ref(false)
const previewHost=ref<HTMLElement>(),zoom=ref(100),saved=ref('[]'),previewSnapshot=ref('[]')
const history=useLocalHistory(pages),changed=computed(()=>JSON.stringify(pages.value)!==saved.value)
const page=computed(()=>pages.value.find(p=>p.unit_id===pageId.value)),current=computed(()=>page.value?.controls[selectedIndex.value])
const imageUrl=computed(()=>props.images.find(i=>i.name===page.value?.image_name)?.url)
const typeOptions=[['button','按钮'],['input','输入框'],['select','下拉框'],['checkbox','复选框'],['radio','单选框'],['link','链接'],['tab','页签'],['table','表格'],['text','文本'],['image','图片'],['other','其他']].map(([value,label])=>({value,label}))
const typeLabel=(value:string)=>typeOptions.find(x=>x.value===value)?.label||value
watch(()=>props.items,items=>{const next=JSON.parse(JSON.stringify(items)).map((p:ControlImage)=>({...p,reviewed:!!p.review}));history.reset(next);saved.value=JSON.stringify(next);if(previewVisible.value)previewSnapshot.value=saved.value;if(!pages.value.some(p=>p.unit_id===pageId.value))pageId.value=pages.value[0]?.unit_id||'';selectedIndex.value=Math.min(selectedIndex.value,Math.max(0,(page.value?.controls.length||0)-1));cancelDraw()},{immediate:true})
watch(changed,value=>emit('dirty',value),{immediate:true,flush:'sync'})
watch(pageId,()=>{selectedIndex.value=Math.max(0,page.value?.controls.findIndex(c=>!c.excluded)??0);cancelDraw();history.endGroup();zoom.value=100})
watch(()=>props.editable,value=>{if(!value){cancelDraw();previewVisible.value=false}})
function updateControl(patch:Partial<VisualControl>,field=''){if(!current.value||!page.value||!props.editable||props.busy)return;history.transaction(()=>{Object.assign(current.value!,patch);page.value!.reviewed=false},field?`${pageId.value}:${selectedIndex.value}:${field}`:'')}
function selectControl(index:number){if(drawing.value)return;history.endGroup();selectedIndex.value=index}
function addControl(){if(!page.value||!props.editable||props.busy)return;cancelDraw();history.transaction(()=>{page.value!.controls.push({id:'',name:'新控件',type:'other',text:'',state:'未知',bbox:null,excluded:false,origin:'human'});page.value!.reviewed=false});selectedIndex.value=page.value.controls.length-1}
function deleteControl(){if(!current.value||!page.value||!props.editable||props.busy)return;cancelDraw();if(current.value.id){updateControl({excluded:true});showExcluded.value=true}else{history.transaction(()=>{page.value!.controls.splice(selectedIndex.value,1);page.value!.reviewed=false});selectedIndex.value=Math.max(0,selectedIndex.value-1)}}
function restoreOriginal(){if(current.value?.original){const {name,type,text,state,bbox,excluded}=current.value.original;updateControl({name,type,text,state,bbox:bbox?[...bbox]:null,excluded})}}
function setReviewed(value:boolean){if(page.value&&!props.busy&&props.editable)history.transaction(()=>{page.value!.reviewed=value})}
function clampSelection(){selectedIndex.value=Math.min(selectedIndex.value,Math.max(0,(page.value?.controls.length||0)-1))}
function undo(){cancelDraw();history.undo();clampSelection()}
function redo(){cancelDraw();history.redo();clampSelection()}
function discard(){cancelDraw();history.reset(JSON.parse(saved.value));clampSelection()}
function openPreview(){cancelDraw();previewSnapshot.value=JSON.stringify(pages.value);zoom.value=100;previewVisible.value=true}
function finishPreview(){cancelDraw();previewVisible.value=false}
function cancelPreview(){cancelDraw();history.transaction(()=>{pages.value=JSON.parse(previewSnapshot.value)});clampSelection();previewVisible.value=false}
function toggleDraw(){if(drawing.value)cancelDraw();else{showBoxes.value=true;drawing.value=true}}
function nextPage(){const pending=pages.value.find(p=>!p.reviewed&&p.unit_id!==pageId.value);if(pending)pageId.value=pending.unit_id}
function boxStyle(box:number[]|null|undefined){return box?{left:`${box[0]!*100}%`,top:`${box[1]!*100}%`,width:`${box[2]!*100}%`,height:`${box[3]!*100}%`}:{display:'none'}}
function point(e:PointerEvent){const rect=(e.currentTarget as HTMLElement).getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(e.clientY-rect.top)/rect.height))]}
function beginDraw(e:PointerEvent){if(!drawing.value||!props.editable||props.busy||!current.value||e.button!==0)return;e.preventDefault();(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);start.value=point(e);draftBox.value=undefined}
function moveDraw(e:PointerEvent){if(!start.value)return;const [x,y]=point(e),[sx,sy]=start.value;draftBox.value=[Math.min(x!,sx!),Math.min(y!,sy!),Math.abs(x!-sx!),Math.abs(y!-sy!)]}
function endDraw(e:PointerEvent){if(!start.value)return;moveDraw(e);if(draftBox.value&&draftBox.value[2]!>0.005&&draftBox.value[3]!>0.005)updateControl({bbox:[...draftBox.value]});cancelDraw()}
function cancelDraw(){drawing.value=false;start.value=undefined;draftBox.value=undefined}
function handleEscape(e:KeyboardEvent){if(e.key==='Escape'&&drawing.value){e.stopImmediatePropagation();e.preventDefault();cancelDraw()}}
onMounted(()=>window.addEventListener('keydown',handleEscape,true))
onUnmounted(()=>window.removeEventListener('keydown',handleEscape,true))
function submit(proceed:boolean){cancelDraw();emit('save',pages.value.map(p=>({unit_id:p.unit_id,controls:p.controls,reviewed:p.reviewed})),proceed)}
</script>
<style scoped>
.control-workspace{color:var(--sgui-text);overflow-wrap:anywhere}.expanded-actions,.history-tools{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:14px 0;flex-wrap:wrap}.expanded-actions>span,.history-tools>span{font-size:12px;color:var(--sgui-muted)}.image-side,.control-side{min-width:0}.expanded .review-layout{grid-template-columns:minmax(0,1fr) 340px}.expanded .control-side{max-height:calc(100vh - 270px);overflow:auto;padding-right:8px}.image-viewport{overflow:auto;border:1px solid var(--sgui-line);background:var(--sgui-soft)}.zoomed-viewport{max-height:65vh}.zoom-tools{display:flex;align-items:center;gap:10px;margin-bottom:10px}@media(max-width:1050px){.expanded .review-layout{grid-template-columns:1fr}.expanded .control-side{max-height:none;overflow:visible}.expanded .image-side{max-width:100%}}
.control-panel{padding:20px;border:1px solid var(--sgui-line);border-radius:12px;background:var(--sgui-surface);margin:16px 0;overflow-wrap:anywhere}.panel-heading,.image-tools,.control-tools,.page-picker{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}h3{margin:0}p{font-size:12px;line-height:1.8;color:var(--sgui-muted)}.page-picker{margin-top:16px}.page-picker>.arco-select{flex:1;min-width:0}.review-layout{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(280px,1fr);gap:24px;align-items:start}.image-board{position:relative;width:100%;line-height:0;user-select:none}.image-board img{display:block;width:100%;height:auto}.image-board.drawing{cursor:crosshair;touch-action:none}.control-box,.draw-box{position:absolute;box-sizing:border-box;border:2px solid #1677ff;background:rgba(22,119,255,.08);padding:0;cursor:pointer;min-width:0;min-height:0}.control-box.selected{border:3px solid #e67e22;background:rgba(230,126,34,.12);z-index:1}.control-box span{white-space:nowrap;position:absolute;left:0;top:0;color:#fff;background:#1664d5;font-size:11px;line-height:17px;padding:0 3px}.drawing .control-box{pointer-events:none}.draw-box{border:2px dashed #e67e22;pointer-events:none}.draw-hint{color:var(--sgui-blue)}.control-list{display:grid;gap:6px;max-height:260px;overflow:auto}.control-list>button{display:flex;align-items:center;gap:10px;text-align:left;padding:10px;border:1px solid var(--sgui-line);border-radius:8px;background:var(--sgui-surface);color:var(--sgui-text);cursor:pointer}.control-list>button.active{border-color:var(--sgui-blue);background:var(--sgui-soft)}.control-list>button.excluded{opacity:.6}.control-list small{display:block;font-size:11px;color:var(--sgui-muted);margin-top:4px}.control-list b{color:var(--sgui-blue)}.control-editor{margin-top:16px}.field-row{display:grid;grid-template-columns:1fr 130px;gap:12px}.linked-cases{margin-top:16px}.linked-cases .arco-link{display:block;margin-top:8px}.review-footer{border-top:1px solid var(--sgui-line);margin-top:20px;padding-top:18px}details{font-size:12px}summary{cursor:pointer}@media(max-width:1050px){.review-layout{grid-template-columns:1fr}.image-side{max-width:760px}.panel-heading,.page-picker,.image-tools{flex-wrap:wrap}}@media(max-width:500px){.control-panel{padding:12px}.field-row{grid-template-columns:1fr}.control-tools{flex-wrap:wrap}}
</style>
