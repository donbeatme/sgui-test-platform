<template>
  <section class="control-panel" aria-label="图片识别与控件核对">
    <div class="panel-heading">
      <div>
        <h3>图片识别与控件核对</h3>
        <p>对照原截图标注，按页面分区核对控件文字、类型与位置。</p>
      </div>
      <a-tag :color="changed?'orange':'blue'">{{ changed?'有未保存修改':`${pages.length} 张图片` }}</a-tag>
    </div>
    <a-alert v-if="editable" type="info">按分区核对文字、控件类型和位置。误识别可删除，漏检控件可新增并在截图上重新框选。</a-alert>
    <a-empty v-if="!pages.length" description="图片解析完成后，这里会显示识别结果。" />

    <div v-if="page" class="control-workspace">
      <div v-if="editable" class="history-tools">
        <a-space wrap>
          <a-button :disabled="busy||!history.canUndo.value" @click="undo">撤销</a-button>
          <a-button :disabled="busy||!history.canRedo.value" @click="redo">重做</a-button>
          <a-popconfirm content="放弃未保存的控件修改，恢复上次保存状态？" @ok="discard">
            <a-button :disabled="busy||!changed">放弃未保存修改</a-button>
          </a-popconfirm>
        </a-space>
        <span>{{ changed?'有未保存修改':'与已保存内容一致' }}</span>
      </div>

      <div class="page-picker">
        <a-select v-model="pageId" :disabled="busy" aria-label="选择核对图片"
          :options="pages.map(p=>({value:p.unit_id,label:`${p.reviewed?'✓ ':''}${p.image_name} · ${p.controls.filter(c=>!c.excluded).length} 个元素`}))" />
        <a-button v-if="editable" :disabled="busy" @click="nextPage">下一张未核对</a-button>
      </div>

      <a-alert v-if="page.legacy" type="warning">此旧任务只保留文字识别结果，没有结构化控件位置。</a-alert>

      <div class="legacy-image-tools">
        <label class="box-toggle"><a-checkbox v-model="showBoxes">在原截图中标注元素位置</a-checkbox></label>
        <span>点击标注可定位到对应元素 · 人工新增控件在列表中标记</span>
        <a-button v-if="editable" type="primary" :disabled="busy||page.controls.length>=60" @click="addControl">新增控件</a-button>
      </div>

      <p v-if="drawing" class="draw-hint" role="status">在截图上按住并拖动鼠标框选「{{ current?.name || '新控件' }}」；松开后保存位置。按 Esc 可取消。</p>
      <div class="legacy-image-wrap">
        <div v-if="imageUrl" class="image-board" role="group" aria-label="当前审核的原始界面截图"
          :class="{drawing}" @pointerdown="beginDraw" @pointermove="moveDraw" @pointerup="endDraw" @pointercancel="cancelDraw">
          <img :src="imageUrl" :alt="page.image_name" draggable="false" />
          <template v-if="showBoxes">
            <button v-for="(c,index) in page.controls" v-show="c.bbox&&!c.excluded" :key="c.id||index" type="button"
              class="control-box" :class="{selected:index===selectedIndex,manual:c.origin==='human'}"
              :style="boxStyle(c.bbox)" :title="`${c.name || c.type} · ${sourceLabel(c.origin)}`"
              :aria-label="`定位控件 ${c.name||index+1}`" @click.stop="selectControl(index)"><span>{{ index+1 }}</span></button>
          </template>
          <div v-if="draftBox" class="draw-box" :style="boxStyle(draftBox)"></div>
        </div>
      </div>

      <div v-if="editable&&current" class="geometry-actions">
        <span>当前：{{ current.name || '未命名控件' }} · {{ regionOptions.find(r=>r.value===(current.region||inferRegion(current.bbox)))?.label }}</span>
        <a-space wrap>
          <a-button :type="drawing?'primary':'secondary'" :disabled="busy||current.excluded" @click="toggleDraw">{{ drawing?'取消框选':'重新框选' }}</a-button>
          <a-button :disabled="busy||!current.bbox" @click="updateControl({bbox:null})">清除框选</a-button>
        </a-space>
      </div>

      <div class="review-heading">
        <div>
          <strong>{{ page.image_name }} · {{ page.controls.filter(c=>!c.excluded).length }} 个元素</strong>
          <p>逐项检查识别结果；修改后保存核对进度，再继续生成用例。</p>
        </div>
        <a-space wrap>
          <a-button size="small" @click="downloadRegionJson">下载完整五分区 JSON</a-button>
          <a-checkbox v-if="page.controls.some(c=>c.excluded)" v-model="showExcluded">显示已删除元素</a-checkbox>
        </a-space>
      </div>

      <section v-for="group in groupedControls" :key="group.value" class="review-section">
        <h3>{{ group.label }} <small>{{ group.items.filter(x=>!x.control.excluded).length }} 个元素</small></h3>
        <div class="review-elements">
          <article v-for="entry in group.items" v-show="showExcluded||!entry.control.excluded" :key="entry.control.id||entry.index"
            class="review-element" :class="{selected:selectedIndex===entry.index,excluded:entry.control.excluded,manual:entry.control.origin==='human'}"
            @click="selectControl(entry.index)">
            <div class="element-preview">
              <img v-if="controlPreviews[entry.index]" :src="controlPreviews[entry.index]" alt="截图中的控件图像" />
              <div v-else class="preview-empty">{{ typeLabel(entry.control.type) }}</div>
            </div>
            <div class="review-fields">
              <label>
                <span>文字 / 名称</span>
                <a-input :model-value="entry.control.text || entry.control.name" :disabled="!editable||busy||entry.control.excluded" :max-length="600"
                  placeholder="无文字图标可填写名称" @update:model-value="updateEntry(entry.index,String($event),'text')" />
              </label>
              <label>
                <span>控件类型</span>
                <a-select :model-value="entry.control.type" :disabled="!editable||busy||entry.control.excluded" :options="typeOptions"
                  @update:model-value="updateEntryType(entry.index,String($event))" />
              </label>
              <label>
                <span>所属区域</span>
                <a-select :model-value="entry.control.region||inferRegion(entry.control.bbox)" :disabled="!editable||busy||entry.control.excluded" :options="regionOptions"
                  @update:model-value="updateEntryRegion(entry.index,String($event))" />
              </label>
            </div>
            <small class="element-meta">
              {{ entry.control.id || '待保存编号' }} · {{ bboxLabel(entry.control.bbox) }}
              <a-tag v-if="entry.control.origin==='human'" size="small" color="green">{{ entry.control.original?'人工修订':'人工新增' }}</a-tag>
              <a-tag v-else-if="entry.control.origin==='perception'" size="small" color="blue">自动识别</a-tag>
              <a-tag v-if="entry.control.excluded" size="small" color="red">已删除</a-tag>
            </small>
            <div v-if="editable" class="element-actions">
              <a-button size="mini" :disabled="busy||entry.control.excluded" @click.stop="startGeometryEdit(entry.index)">编辑 / 重新框选</a-button>
              <a-button v-if="entry.control.excluded" size="mini" :disabled="busy" @click.stop="restoreEntry(entry.index)">恢复</a-button>
              <a-button v-else size="mini" status="danger" :disabled="busy" @click.stop="deleteEntry(entry.index)">删除</a-button>
            </div>
          </article>
        </div>
      </section>

      <a-empty v-if="!page.controls.length" description="没有识别到控件，可在原图上新增控件。" />

      <div v-if="current" class="selected-detail">
        <strong>当前元素详情</strong>
        <div class="detail-grid">
          <label><span>可见状态</span><a-input :model-value="current.state" :disabled="!editable||busy" :max-length="300" @update:model-value="updateControl({state:String($event)},'state')" /></label>
          <label><span>控件名称</span><a-input :model-value="current.name" :disabled="!editable||busy" :max-length="120" @update:model-value="updateControl({name:String($event)},'name')" /></label>
        </div>
        <p>来源：{{ sourceLabel(current.origin) }}；当前框选：{{ bboxLabel(current.bbox) }}。</p>
        <details v-if="current.original"><summary>查看识别原值</summary><p>{{ current.original.name }} · {{ typeLabel(current.original.type) }} · {{ bboxLabel(current.original.bbox) }}</p></details>
      </div>

      <div v-if="editable" class="review-footer">
        <a-checkbox :model-value="page.reviewed" :disabled="busy" @update:model-value="setReviewed(!!$event)">已核对本图识别结果</a-checkbox>
        <p>已核对 {{ pages.filter(p=>p.reviewed).length }}/{{ pages.length }} 张。内容修改后，本图需要重新勾选。</p>
        <a-space wrap>
          <a-button :loading="busy" @click="submit(false)">保存核对进度</a-button>
          <a-button type="primary" :loading="busy" :disabled="!pages.length||pages.some(p=>!p.reviewed)" @click="submit(true)">按核对结果继续生成</a-button>
        </a-space>
      </div>
      <p v-if="page.review">最近核对：{{ page.review.username }} · {{ new Date(page.review.at).toLocaleString() }} · {{ page.edit_count }} 次内容修订</p>
    </div>
  </section>
</template>
<script lang="ts">
export interface VisualControl {id:string;name:string;type:string;text:string;state:string;bbox:number[]|null;region?:string;excluded:boolean;origin?:string;original?:VisualControl|null;case_ids?:string[];case_names?:string[]}
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
const regionOptions=[['left_global_navigation','左侧全局导航'],['top_navigation_toolbar','顶部导航与工具栏'],['tab_area','标签页区'],['main_workspace','主工作区'],['bottom_action_bar','底部操作栏']].map(([value,label])=>({value,label}))
function inferRegion(box:number[]|null|undefined){if(!box)return 'main_workspace';const x=box[0]!+box[2]!/2,y=box[1]!+box[3]!/2;return y>=.86?'bottom_action_bar':x<.18?'left_global_navigation':y<.10?'top_navigation_toolbar':y<.20?'tab_area':'main_workspace'}
const groupedControls=computed(()=>regionOptions.map(region=>({ ...region, items:(page.value?.controls||[]).map((control,index)=>({control,index})).filter(({control})=>(control.region||inferRegion(control.bbox))===region.value)})).filter(group=>group.items.length))
const typeOptions=[['button','按钮'],['input','输入框'],['select','下拉框'],['checkbox','复选框'],['radio','单选框'],['switch','开关'],['link','链接'],['tab','页签'],['table','表格'],['text','文本'],['image','图片'],['icon','图标待细分'],['control','控件待细分'],['other','其他']].map(([value,label])=>({value,label}))
const typeLabel=(value:string)=>typeOptions.find(x=>x.value===value)?.label||value
const sourceLabel=(value?:string)=>value==='human'?'人工补充 / 修正':value==='legacy'?'旧版文字分析':value==='perception'?'本地感知识别':'视觉模型识别'
const controlPreviews=ref<Record<number,string>>({})
function bboxLabel(box:number[]|null|undefined){return box?.length===4?box.map(v=>Number(v).toFixed(3)).join(', '):'无框选'}
function updateEntry(index:number,value:string,field:'text'|'name'='text'){
  if(!page.value||!props.editable||props.busy)return
  selectControl(index)
  const c=page.value.controls[index]
  if(!c)return
  if(field==='text') updateControl({text:value,name:value.trim()||c.name},'text')
  else updateControl({name:value},'name')
}
function updateEntryType(index:number,value:string){selectControl(index);updateControl({type:value})}
function updateEntryRegion(index:number,value:string){selectControl(index);updateControl({region:value})}
function deleteEntry(index:number){cancelDraw();selectControl(index);deleteControl()}
function restoreEntry(index:number){cancelDraw();selectControl(index);updateControl({excluded:false})}
function startGeometryEdit(index:number){cancelDraw();selectControl(index);showBoxes.value=true;drawing.value=true;window.scrollTo({top:0,behavior:'smooth'})}
function rebuildControlPreviews(){
  controlPreviews.value={}
  const url=imageUrl.value, controls=page.value?.controls||[]
  if(!url||!controls.length)return
  const image=new Image();image.crossOrigin='anonymous'
  image.onload=()=>{
    const out:Record<number,string>={}
    controls.forEach((control,index)=>{
      const box=control.bbox
      if(!box||box.length!==4)return
      const [x,y,w,h]=box
      const sx=Math.max(0,Math.floor(x!*image.naturalWidth)),sy=Math.max(0,Math.floor(y!*image.naturalHeight))
      const sw=Math.min(image.naturalWidth-sx,Math.ceil(w!*image.naturalWidth)),sh=Math.min(image.naturalHeight-sy,Math.ceil(h!*image.naturalHeight))
      if(sw<=0||sh<=0)return
      const scale=Math.min(1,96/Math.max(sw,sh)),canvas=document.createElement('canvas')
      canvas.width=Math.max(1,Math.round(sw*scale));canvas.height=Math.max(1,Math.round(sh*scale))
      try{canvas.getContext('2d')?.drawImage(image,sx,sy,sw,sh,0,0,canvas.width,canvas.height);out[index]=canvas.toDataURL('image/png')}catch{}
    })
    controlPreviews.value=out
  }
  image.src=url
}
watch(()=>[imageUrl.value,(page.value?.controls||[]).map(c=>c.bbox?.join(',')||'').join('|')],rebuildControlPreviews,{immediate:true})
const iconPreview=ref('')
watch(()=>[imageUrl.value,current.value?.bbox?.join(',')||''],([url],_,onCleanup)=>{
  iconPreview.value=''
  const box=current.value?.bbox
  if(!url||!box)return
  let active=true
  onCleanup(()=>{active=false})
  const image=new Image()
  image.crossOrigin='anonymous'
  image.onload=()=>{
    if(!active)return
    const [x,y,w,h]=box
    const sx=Math.floor(x!*image.naturalWidth),sy=Math.floor(y!*image.naturalHeight)
    const sw=Math.min(image.naturalWidth-sx,Math.ceil(w!*image.naturalWidth)),sh=Math.min(image.naturalHeight-sy,Math.ceil(h!*image.naturalHeight))
    if(sw<=0||sh<=0)return
    const scale=Math.min(1,128/Math.max(sw,sh)),canvas=document.createElement('canvas')
    canvas.width=Math.max(1,Math.round(sw*scale));canvas.height=Math.max(1,Math.round(sh*scale))
    try{canvas.getContext('2d')?.drawImage(image,sx,sy,sw,sh,0,0,canvas.width,canvas.height);iconPreview.value=canvas.toDataURL('image/png')}catch{iconPreview.value=''}
  }
  image.src=url
},{immediate:true})
watch(()=>props.items,items=>{const next=JSON.parse(JSON.stringify(items)).map((p:ControlImage)=>({...p,reviewed:!!p.review}));history.reset(next);saved.value=JSON.stringify(next);if(previewVisible.value)previewSnapshot.value=saved.value;if(!pages.value.some(p=>p.unit_id===pageId.value))pageId.value=pages.value[0]?.unit_id||'';selectedIndex.value=Math.min(selectedIndex.value,Math.max(0,(page.value?.controls.length||0)-1));cancelDraw()},{immediate:true})
watch(changed,value=>emit('dirty',value),{immediate:true,flush:'sync'})
watch(pageId,()=>{selectedIndex.value=Math.max(0,page.value?.controls.findIndex(c=>!c.excluded)??0);cancelDraw();history.endGroup();zoom.value=100})
watch(()=>props.editable,value=>{if(!value){cancelDraw();previewVisible.value=false}})
function updateControl(patch:Partial<VisualControl>,field=''){if(!current.value||!page.value||!props.editable||props.busy)return;history.transaction(()=>{Object.assign(current.value!,patch);page.value!.reviewed=false},field?`${pageId.value}:${selectedIndex.value}:${field}`:'')}
function selectControl(index:number){if(drawing.value)return;history.endGroup();selectedIndex.value=index}
function addControl(){if(!page.value||!props.editable||props.busy)return;cancelDraw();history.transaction(()=>{page.value!.controls.push({id:'',name:'新控件',type:'control',text:'',state:'未知',bbox:null,region:'main_workspace',excluded:false,origin:'human'});page.value!.reviewed=false});selectedIndex.value=page.value.controls.length-1}
function deleteControl(){if(!current.value||!page.value||!props.editable||props.busy)return;cancelDraw();if(current.value.id){updateControl({excluded:true});showExcluded.value=true}else{history.transaction(()=>{page.value!.controls.splice(selectedIndex.value,1);page.value!.reviewed=false});selectedIndex.value=Math.max(0,selectedIndex.value-1)}}
function restoreOriginal(){if(current.value?.original){const {name,type,text,state,bbox,region,excluded}=current.value.original;updateControl({name,type,text,state,bbox:bbox?[...bbox]:null,region,excluded})}}
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
function downloadRegionJson(){
  if(!page.value)return
  const regions:Record<string,unknown[]>=Object.fromEntries(regionOptions.map(r=>[r.value,[]]))
  for(const c of page.value.controls.filter(c=>!c.excluded)){
    const region=c.region||inferRegion(c.bbox)
    regions[region]?.push({id:c.id,name:c.name,type:c.type,text:c.text,state:c.state,bbox:c.bbox,origin:c.origin})
  }
  const data={image_name:page.value.image_name,regions}
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json;charset=utf-8'}))
  const a=document.createElement('a');a.href=url;a.download=`${page.value.image_name.replace(/[^\w.-]/g,'_')}.controls.json`;a.click()
  setTimeout(()=>URL.revokeObjectURL(url),1000)
}
function boxStyle(box:number[]|null|undefined){return box?{left:`${box[0]!*100}%`,top:`${box[1]!*100}%`,width:`${box[2]!*100}%`,height:`${box[3]!*100}%`}:{display:'none'}}
function point(e:PointerEvent){const rect=(e.currentTarget as HTMLElement).getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(e.clientY-rect.top)/rect.height))]}
function beginDraw(e:PointerEvent){if(!drawing.value||!props.editable||props.busy||!current.value||e.button!==0)return;e.preventDefault();(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);start.value=point(e);draftBox.value=undefined}
function moveDraw(e:PointerEvent){if(!start.value)return;const [x,y]=point(e),[sx,sy]=start.value;draftBox.value=[Math.min(x!,sx!),Math.min(y!,sy!),Math.abs(x!-sx!),Math.abs(y!-sy!)]}
function endDraw(e:PointerEvent){if(!start.value)return;moveDraw(e);if(draftBox.value&&draftBox.value[2]!>0.005&&draftBox.value[3]!>0.005){const bbox=[...draftBox.value];updateControl(current.value?.bbox?{bbox}:{bbox,region:inferRegion(bbox)})}cancelDraw()}
function cancelDraw(){drawing.value=false;start.value=undefined;draftBox.value=undefined}
function handleEscape(e:KeyboardEvent){if(e.key==='Escape'&&drawing.value){e.stopImmediatePropagation();e.preventDefault();cancelDraw()}}
onMounted(()=>window.addEventListener('keydown',handleEscape,true))
onUnmounted(()=>window.removeEventListener('keydown',handleEscape,true))
function submit(proceed:boolean){cancelDraw();emit('save',pages.value.map(p=>({unit_id:p.unit_id,controls:p.controls,reviewed:p.reviewed})),proceed)}
</script>
<style scoped>
.control-panel{padding:20px;border:1px solid var(--sgui-line);border-radius:12px;background:var(--sgui-surface);margin:16px 0;overflow-wrap:anywhere}.control-workspace{color:var(--sgui-text)}
.panel-heading,.history-tools,.page-picker,.legacy-image-tools,.review-heading,.geometry-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.panel-heading{margin-bottom:14px}.history-tools{margin:14px 0}.history-tools>span,.legacy-image-tools>span{font-size:12px;color:var(--sgui-muted)}.page-picker{margin:16px 0}.page-picker>.arco-select{flex:1;min-width:260px}h3{margin:0}p{font-size:12px;line-height:1.8;color:var(--sgui-muted)}
.legacy-image-tools{margin:16px 0 8px}.box-toggle{display:flex;align-items:center}.legacy-image-wrap{max-height:570px;overflow:auto;border:1px solid var(--sgui-line);background:var(--sgui-soft);border-radius:8px;padding:8px}.image-board{position:relative;display:table;margin:auto;line-height:0;max-width:100%;user-select:none}.image-board>img{display:block;max-width:100%;height:auto}.image-board.drawing{cursor:crosshair;touch-action:none}.control-box,.draw-box{position:absolute;box-sizing:border-box;border:1px solid rgba(37,99,235,.8);background:rgba(37,99,235,.06);padding:0;min-width:2px;min-height:2px;cursor:pointer}.control-box.selected{z-index:2;border:2px solid #e84d62;background:rgba(232,77,98,.13)}.control-box.manual{border-color:#16835a;background:rgba(22,131,90,.10)}.control-box span{position:absolute;left:0;top:0;color:#fff;background:#1664d5;font-size:10px;line-height:15px;padding:0 3px}.control-box.manual span{background:#16835a}.drawing .control-box{pointer-events:none}.draw-box{z-index:3;border:2px dashed #16835a;background:rgba(22,131,90,.12);pointer-events:none}.draw-hint{font-size:13px;color:#16835a}.geometry-actions{margin:10px 0 18px;padding:10px 12px;border:1px solid var(--sgui-line);border-radius:6px;background:var(--sgui-soft);font-size:12px}
.review-heading{margin:20px 0 8px}.review-heading p{margin:2px 0 0}.review-section{margin-top:18px}.review-section>h3{font-size:15px;margin-bottom:10px}.review-section>h3 small{font-weight:400;color:var(--sgui-muted);margin-left:6px}.review-elements{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}.review-element{position:relative;display:grid;grid-template-columns:76px minmax(0,1fr);gap:10px;padding:12px;border:1px solid var(--sgui-line);border-radius:8px;background:var(--sgui-surface);cursor:pointer}.review-element.selected{border:2px solid #e84d62;padding:11px;background:rgba(232,77,98,.03)}.review-element.manual{border-left:4px solid #16835a}.review-element.excluded{opacity:.58}.element-preview{width:72px;height:72px;display:flex;align-items:center;justify-content:center;overflow:hidden;border:1px solid var(--sgui-line);border-radius:6px;background:#fff}.element-preview img{display:block;max-width:100%;max-height:100%;object-fit:contain}.preview-empty{font-size:11px;color:var(--sgui-muted);text-align:center;padding:4px}.review-fields{display:grid;grid-template-columns:minmax(0,1fr) 120px;gap:8px}.review-fields label{display:flex;flex-direction:column;gap:4px;min-width:0}.review-fields label:nth-child(3){grid-column:1/-1}.review-fields label>span{font-size:11px;color:var(--sgui-muted)}.element-meta{grid-column:1/-1;color:var(--sgui-muted);display:flex;align-items:center;gap:6px;flex-wrap:wrap}.element-actions{grid-column:1/-1;display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}.selected-detail{margin-top:18px;padding:14px;border:1px solid var(--sgui-line);border-radius:8px;background:var(--sgui-soft)}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px}.detail-grid label{display:flex;flex-direction:column;gap:5px}.detail-grid label>span{font-size:12px;color:var(--sgui-muted)}.review-footer{border-top:1px solid var(--sgui-line);margin-top:20px;padding-top:18px}details{font-size:12px}summary{cursor:pointer}
@media(max-width:800px){.review-elements{grid-template-columns:1fr}.review-fields{grid-template-columns:1fr}.review-fields label:nth-child(3){grid-column:auto}.detail-grid{grid-template-columns:1fr}.page-picker>.arco-select{min-width:0;width:100%}.control-panel{padding:12px}}
</style>
