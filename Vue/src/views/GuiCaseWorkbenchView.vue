<template>
  <div class="gc-workbench" role="region" aria-label="图文用例工作台内容" tabindex="0">
    <div class="gc-heading">
      <div><div class="gc-eyebrow">SGUI · 用例工程</div><h1>图文用例工作台</h1><p>整理需求与界面，生成可评审、可追溯的测试用例。</p></div>
      <a-space><a-button @click="tab = 'history'">生成记录</a-button><a-button @click="startNew">新建任务</a-button><a-button type="primary" @click="router.push('/testcases')">用例管理 <icon-arrow-right /></a-button></a-space>
    </div>
    <a-alert v-if="!projectId" type="info">请先在顶部选择项目，或到项目管理中创建项目。<a-button type="text" @click="router.push('/projects')">项目管理</a-button></a-alert>
    <a-alert v-if="error" type="error" closable @close="error = ''" class="gc-error">{{ error }}</a-alert>
    <div v-if="projectId" class="gc-grid">
      <aside class="gc-guide">
        <div class="gc-guide-title">{{ projectStore.currentProject?.name }}</div>
        <button v-for="(item, i) in journey" :key="item.title" class="gc-journey" :class="{active: tab === item.tab}" @click="tab = item.tab"><span>{{ i + 1 }}</span><div><strong>{{ item.title }}</strong><small>{{ item.desc }}</small></div></button>
        <div class="gc-guide-divider"></div>
        <div class="gc-guide-caption">测试资源</div>
        <router-link to="/requirements"><icon-file /> 需求文档与评审</router-link>
        <router-link to="/knowledge-management"><icon-book /> 知识库</router-link>
        <router-link to="/ui-automation"><icon-computer /> UI 自动化</router-link>
        <router-link to="/api-testing"><icon-cloud /> 接口自动化</router-link>
        <router-link to="/task-center"><icon-schedule /> 定时任务</router-link>
        <div class="gc-guide-tip">生成结果先进入草稿。正式用例、执行记录与报告在平台中统一管理。</div>
      </aside>
      <main class="gc-panel">
        <a-tabs v-model:active-key="tab">
          <a-tab-pane key="input" title="需求与图片">
            <div class="gc-pane">
              <a-form layout="vertical">
                <a-alert class="gc-space-bottom">上传需求文档即可开始，图片可选。任务名称与需求说明均可留空；AI 会根据材料归纳任务名称。</a-alert>
                <a-form-item label="任务名称（可选）"><a-input v-model="title" :disabled="busy" placeholder="留空，由 AI 根据需求材料自动命名" :max-length="200" /></a-form-item>
                <a-form-item label="需求说明（可选）"><a-textarea v-model="requirement" :disabled="busy" placeholder="已有需求文档时可留空；也可补充测试范围、重点或限制。没有文档时，可直接在这里输入需求。" :auto-size="{minRows: 4, maxRows: 12}" :max-length="limits.text_chars" show-word-limit /></a-form-item>
                <div class="gc-two-col">
                  <div class="gc-upload" :class="{'gc-drop-active': dragging === 'docs'}" @dragover.prevent="dragging = 'docs'" @dragleave.prevent="dragging = ''" @drop.prevent="dropFiles($event, 'docs')">
                    <strong><icon-file /> 需求文档</strong><p>TXT / MD / PDF / DOCX · 最多 {{ limits.documents }} 份（含已有文档）· 单份 ≤ {{ limits.document_bytes / MB }} MB</p>
                    <input ref="docPicker" class="gc-hidden-picker" aria-label="上传需求文档" type="file" multiple accept=".txt,.md,.pdf,.docx" :disabled="busy" @change="pickFiles($event, 'docs')" />
                    <div class="gc-upload-tools"><a-button :disabled="busy" @click="docPicker?.click()"><icon-plus /> {{ docs.length ? '继续添加文档' : '选择需求文档' }}</a-button><a-button v-if="docs.length" type="text" :disabled="busy" @click="docs = []">清空待上传文档</a-button></div>
                    <p>支持多选、分批追加或拖放文件；点击生成前可随时移除。</p>
                    <ul v-if="docs.length" class="gc-file-list" aria-label="待上传需求文档"><li v-for="(f, index) in docs" :key="fileKey(f)"><div><strong>{{ f.name }}</strong><small>{{ formatSize(f.size) }} · 待上传</small></div><a-button type="text" status="danger" :disabled="busy" :aria-label="`移除文档 ${f.name}`" @click="docs.splice(index, 1)">移除</a-button></li></ul>
                  </div>
                  <div class="gc-upload" :class="{'gc-drop-active': dragging === 'images'}" @dragover.prevent="dragging = 'images'" @dragleave.prevent="dragging = ''" @drop.prevent="dropFiles($event, 'images')">
                    <strong><icon-image /> UI 界面截图 <a-tag size="small" color="blue">可选</a-tag></strong><p>JPG / PNG / WebP · 最多 {{ limits.images }} 张 · 单张 ≤ {{ limits.image_bytes / MB }} MB</p>
                    <input ref="imagePicker" class="gc-hidden-picker" aria-label="上传界面截图" type="file" multiple accept="image/png,image/jpeg,image/webp" :disabled="busy" @change="pickFiles($event, 'images')" />
                    <div class="gc-upload-tools"><a-button :disabled="busy" @click="imagePicker?.click()"><icon-plus /> {{ images.length ? '继续添加图片' : '选择界面图片' }}</a-button><a-button v-if="images.length" type="text" :disabled="busy" @click="clearImages">清空图片</a-button></div>
                    <p>图片按每批最多 {{ limits.vision_batch_size }} 张依次解析，完成的批次会保存，可从断点继续。全部分析结果一起用于 AI 模块规划；图片越多，生成时间和模型调用量越大。</p>
                    <div class="gc-images"><div v-for="(img, index) in imagePreviews" :key="img.url"><img :src="img.url" :alt="img.name" loading="lazy" decoding="async" /><small :title="img.name">{{ img.name }}</small><a-button type="text" status="danger" size="mini" :disabled="busy" :aria-label="`移除图片 ${img.name}`" @click="removeImage(index)">移除</a-button></div></div>
                  </div>
                </div>
                <a-alert v-if="fileError" type="warning" class="gc-space-bottom" closable @close="fileError = ''">{{ fileError }}</a-alert>
                <a-form-item label="引用已有需求文档（可选）"><a-select v-model="documentIds" multiple allow-clear :disabled="busy" placeholder="可多选，点击标签 × 移除；来自当前项目的需求管理" :options="documentOptions" /></a-form-item>
                <p class="gc-material-summary" aria-live="polite">已选 {{ docs.length + documentIds.length }}/{{ limits.documents }} 份文档、{{ images.length }}/{{ limits.images }} 张图片 · 待上传 {{ formatSize(uploadBytes) }}/{{ limits.upload_bytes / MB }} MB。合并文字上限 {{ (limits.context_chars / 10000).toLocaleString() }} 万字符；按标题、表格与完整用例组织材料，由 AI 规划模块后逐模块生成。过长单元会保留接续上下文，原文保留供评审。</p>
                <a-checkbox v-model="includeDocumentImages">同时分析所选 DOCX 文档的内嵌图片（与上传截图合计最多 {{ limits.images }} 张）</a-checkbox>
                <div class="gc-two-col gc-space-top">
                  <a-form-item label="文字生成模型"><a-select v-model="textModelId" allow-clear placeholder="沿用阶段设置 / 当前激活模型" :options="modelOptions" /></a-form-item>
                  <a-form-item label="图片解析模型"><a-select v-model="visionModelId" allow-clear placeholder="选择支持图片输入的模型" :options="visionModelOptions" /></a-form-item>
                </div>
                <a-alert v-if="(images.length || includeDocumentImages) && !visionModelOptions.length" type="warning">尚无支持图片输入的模型。请在系统管理 → 模型配置中添加视觉模型，再使用图片生成。</a-alert>
                <a-form-item label="补充知识库（可选）"><a-select v-model="knowledgeBaseId" allow-clear placeholder="选择当前项目的知识库" :options="knowledgeOptions" /></a-form-item>
              </a-form>
              <div class="gc-actions"><span>{{ busy ? '正在上传并解析材料，请稍候…' : '点击生成后，文档将保存到当前项目的需求管理中。' }}</span><a-space wrap><a-button :disabled="busy" @click="fillExample">填入文字示例</a-button><a-button type="primary" :loading="busy" @click="generate"><icon-thunderbolt /> 生成用例草稿</a-button></a-space></div>
            </div>
          </a-tab-pane>
          <a-tab-pane key="draft" title="用例结果">
            <div class="gc-pane">
              <a-empty v-if="!job" description="开始一次生成，或从生成记录中打开草稿。" />
              <template v-else>
                <div class="gc-result-heading"><div><h2>{{ job.title }}</h2><p>{{ job.stage }}</p></div><a-tag :color="statusColor(job.status)">{{ statusLabel(job.status) }}</a-tag></div>
                <a-alert v-if="running" type="info"><a-spin /> 后台正在处理，离开页面后任务会继续。<a-button type="text" @click="cancelJob">取消后续处理</a-button></a-alert>
                <p v-if="job.progress?.vision?.image_total" class="gc-material-summary" aria-live="polite">图片解析已完成 {{ job.progress.vision.image_done }}/{{ job.progress.vision.image_total }} 张<span v-if="job.progress.vision.batch_total"> · {{ job.progress.vision.batch_done }}/{{ job.progress.vision.batch_total }} 批</span>。完成的分析已保存，后续统一规划业务模块。</p>
                <a-alert v-if="job.status === 'failed' || job.status === 'cancelled'" type="warning" class="gc-space-bottom">{{ job.progress?.last_error || '任务未完成，已保留可用进度。' }}<a-space wrap><a-button v-if="job.progress?.resumable" type="primary" :loading="busy" @click="resumeJob">从断点继续</a-button><a-button type="text" :disabled="busy" @click="restoreMaterials">调整材料后新建任务</a-button></a-space></a-alert>
                <a-collapse v-if="job.progress?.modules?.length" class="gc-space-top gc-space-bottom" :default-active-key="['plan']">
                  <a-collapse-item key="plan" header="AI 模块规划与生成进度">
                    <p>已完成 {{ job.progress.batch_done }}/{{ job.progress.batch_total }} 批。按业务模块组织资料，已完成的结果会保存；原文来源随用例保留。</p>
                    <div v-for="module in job.progress.modules" :key="module.id" class="gc-module-progress"><div><strong>{{ module.path }}</strong><p>{{ module.description }}</p></div><a-tag :color="module.batch_done === module.batch_total ? 'green' : 'blue'">{{ module.batch_done }}/{{ module.batch_total }} 批 · {{ module.case_count }} 条</a-tag></div>
                    <p v-if="job.progress.coverage">业务材料 {{ job.progress.coverage.assigned_units }} 个单元，生成用例显式引用 {{ job.progress.coverage.cited_units }} 个。其余单元和跨模块流程需继续检查覆盖；材料已归类不代表需求已全部测试。</p>
                  </a-collapse-item>
                </a-collapse>
                <div v-if="job.cases.length" class="gc-metrics"><div><b>{{ job.cases.length }}</b><span>条用例</span></div><div><b>{{ job.issues?.errors || 0 }}</b><span>个规则错误</span></div><div><b>{{ job.issues?.warnings || 0 }}</b><span>条改进建议</span></div><div><b>{{ job.issues?.duplicates?.length || 0 }}</b><span>条重复来源</span></div></div>
                <div v-if="job.cases.length" class="gc-result-tools"><a-space><a-input v-model="caseSearch" placeholder="搜索名称或模块" allow-clear style="width:230px" /><a-button v-if="job.status === 'draft'" :loading="busy" @click="saveDraft">校验并保存草稿</a-button></a-space><a-space><a-dropdown @select="download"><a-button>导出 <icon-down /></a-button><template #content><a-doption value="xlsx">Excel</a-doption><a-doption value="docx">Word</a-doption><a-doption value="json">JSON（完整字段）</a-doption><a-doption value="feature">Gherkin Feature</a-doption></template></a-dropdown><a-button v-if="job.status === 'draft'" type="primary" :loading="busy" @click="commit">确认存入用例管理</a-button><a-button v-else-if="job.status === 'saved'" type="primary" @click="router.push('/testcases')">查看正式用例</a-button></a-space></div>
                <a-alert v-if="dirty" type="warning" class="gc-space-bottom">草稿有未保存的修改。存入用例管理或导出前会先保存并重新校验。</a-alert>
                <a-table v-if="job.cases.length" :data="filteredCases" :pagination="{pageSize:10,showTotal:true}" row-key="tc_sno" :scroll="{x:780}">
                  <template #columns><a-table-column title="用例名称" data-index="tc_name" :width="320"><template #cell="{record}"><a-link @click="editCase(record.tc_sno)">{{ record.tc_name }}</a-link><small class="gc-subtext">{{ record.tc_sno }}</small></template></a-table-column><a-table-column title="模块" data-index="module_path" :width="220" /><a-table-column title="级别" data-index="level" :width="65" /><a-table-column title="场景" data-index="judge_type" :width="70" /><a-table-column title="步骤" :width="60"><template #cell="{record}">{{ record.steps.length }}</template></a-table-column><a-table-column title="操作" :width="90"><template #cell="{record}"><a-button type="text" @click="editCase(record.tc_sno)">{{ job.status === 'draft' ? '编辑' : '详情' }}</a-button></template></a-table-column></template>
                </a-table>
                <a-collapse class="gc-space-top">
                  <a-collapse-item v-if="job.issues?.detail?.length" key="issues" header="查看规则问题"><div v-for="(item,i) in job.issues.detail" :key="i" class="gc-issue"><strong>{{ item.case }}</strong><div v-for="(issue,j) in item.issues" :key="j"><a-tag :color="issue.level === 'error' ? 'red' : 'orange'" size="small">{{ issue.level === 'error' ? '错误' : '建议' }}</a-tag> {{ issue.message }}</div></div></a-collapse-item>
                  <a-collapse-item key="notes" header="生成过程与来源说明"><pre v-for="(note,i) in job.notes" :key="i" class="gc-note">{{ note }}</pre><p v-if="!job.notes.length">本任务暂无附加记录。</p></a-collapse-item>
                </a-collapse>
                <div v-if="job.status === 'saved'" class="gc-next"><strong>接下来：评审与执行</strong><p>正式用例已进入待审核状态。需要自动执行时，在 SGUI 中配置页面、元素与 UI 步骤，或使用智能助手和测试套件执行。</p><a-space><a-button @click="router.push('/ui-automation')">UI 自动化</a-button><a-button @click="router.push('/testsuites')">测试套件</a-button><a-button @click="router.push('/langgraph-chat')">智能助手</a-button></a-space></div>
              </template>
            </div>
          </a-tab-pane>
          <a-tab-pane key="history" title="生成记录与导入">
            <div class="gc-pane"><div class="gc-import-row"><div><h2>继续工作或导入用例</h2><p>保留源编号、模块层级、测试分类、动作信息及截图引用。导入后先预览，再确认入库。</p></div><a-space><a-button :loading="busy" @click="loadPreset">载入 43 条预置用例</a-button><label class="gc-file-button">导入 JSON<input aria-label="导入用例 JSON" type="file" accept=".json,application/json" @change="importJson" /></label></a-space></div>
              <a-table :data="jobs" row-key="id" :pagination="{pageSize:10}"><template #columns><a-table-column title="任务名称" data-index="title"><template #cell="{record}"><a-link @click="openJob(record.id)">{{ record.title }}</a-link></template></a-table-column><a-table-column title="状态"><template #cell="{record}"><a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag></template></a-table-column><a-table-column title="用例数" data-index="count" /><a-table-column title="创建时间"><template #cell="{record}">{{ new Date(record.created_at).toLocaleString() }}</template></a-table-column></template></a-table>
            </div>
          </a-tab-pane>
          <a-tab-pane key="settings" title="模型、规则与提示词">
            <div class="gc-pane"><a-alert>模型连接统一维护在 SGUI 中。这里为不同生成阶段选择模型；Embedding 沿用知识库配置。</a-alert><a-button class="gc-space-top" @click="router.push('/llm-configs')">打开模型配置</a-button>
              <div class="gc-two-col gc-space-top"><a-form-item v-for="stage in stages" :key="stage.key" :label="stage.label"><a-select v-model="config.stages[stage.key]" :disabled="!canConfigure" allow-clear :placeholder="stage.placeholder" :options="stage.key === 'vision' ? visionModelOptions : modelOptions" /></a-form-item></div>
              <a-collapse><a-collapse-item v-for="prompt in prompts" :key="prompt.key" :header="prompt.label"><a-textarea v-model="config.prompts[prompt.key]" :disabled="!canConfigure" :auto-size="{minRows:4,maxRows:12}" :max-length="10000" /></a-collapse-item><a-collapse-item key="rules" :header="`校验规则 · ${config.rules.length} 条`"><p>支持必填、枚举、正则匹配、正则排除、长度和数量六类规则。错误阻止入库，建议允许入库。结构必填项始终生效。</p><a-table :data="config.rules" :pagination="false" row-key="id"><template #columns><a-table-column title="启用" :width="70"><template #cell="{record}"><a-switch v-model="record.enabled" size="small" :disabled="!canConfigure" /></template></a-table-column><a-table-column title="字段" data-index="field" /><a-table-column title="类型" data-index="type" /><a-table-column title="级别" :width="120"><template #cell="{record}"><a-select v-model="record.level" :disabled="!canConfigure" :options="[{label:'错误',value:'error'},{label:'建议',value:'warn'}]" /></template></a-table-column><a-table-column title="提示" data-index="message" /></template></a-table><a-button class="gc-space-top" @click="openRulesEditor">编辑规则定义 / 添加规则</a-button></a-collapse-item></a-collapse>
              <div class="gc-actions"><span>设置按项目保存，仅对新任务生效。</span><a-button type="primary" :disabled="!canConfigure" :loading="busy" @click="saveSettings">保存工作台设置</a-button></div>
            </div>
          </a-tab-pane>
        </a-tabs>
      </main>
    </div>
    <a-drawer v-model:visible="editorVisible" width="680px" :title="job?.status === 'saved' ? '正式用例详情' : '编辑草稿用例'" :footer="job?.status !== 'saved'" @ok="applyEdit">
      <a-form v-if="editing" layout="vertical" :disabled="job?.status === 'saved'">
        <a-alert v-if="editing.source_references?.length" class="gc-space-bottom"><strong>原文依据</strong><p v-for="(source,index) in editing.source_references" :key="index">{{ source.document }}<span v-if="source.pages?.length"> · 第 {{ source.pages.join('、') }} 页</span><span v-if="source.source_case_id"> · 原用例 {{ source.source_case_id }}</span> · {{ source.unit_id }}</p></a-alert>
        <a-form-item label="源编号"><a-input v-model="editing.tc_sno" disabled /></a-form-item><a-form-item label="名称"><a-input v-model="editing.tc_name" :max-length="255" /></a-form-item><a-form-item label="模块路径（/ 分隔，最多 5 层）"><a-input v-model="editing.module_path" /></a-form-item>
        <div class="gc-two-col"><a-form-item label="用例优先级"><a-select v-model="editing.level" :options="['P0','P1','P2','P3']" @change="syncPriority" /></a-form-item><a-form-item label="测试类型"><a-select v-model="editing.test_type" :options="testTypes" /></a-form-item><a-form-item label="场景分类"><a-select v-model="editing.judge_type" :options="['正常场景','异常场景']" /></a-form-item><a-form-item label="数据分类"><a-select v-model="editing.data_type" :options="['典型值','边界值','枚举值']" /></a-form-item><a-form-item label="测试目的"><a-select v-model="editing.objective" :options="['验证新功能','回归测试','其它']" /></a-form-item><a-form-item label="执行方式标记"><a-select v-model="editing.automation" :options="['手工','自动化']" /></a-form-item></div>
        <a-form-item label="前置条件"><a-textarea v-model="editing.precondition" /></a-form-item><a-form-item label="摘要"><a-textarea v-model="editing.summary" /></a-form-item><a-form-item label="目的细分"><a-input v-model="editing.sub_objective" /></a-form-item><a-form-item label="标签"><a-input-tag v-model="editing.tags" /></a-form-item><a-form-item label="截图引用"><a-input-tag v-model="editing.source_images" /></a-form-item>
        <div v-for="(step,i) in editing.steps" :key="i" class="gc-step"><div class="gc-step-title"><strong>步骤 {{ i + 1 }}</strong><a-button v-if="job?.status !== 'saved'" type="text" status="danger" @click="editing.steps.splice(i,1)">删除</a-button></div><a-form-item label="操作描述"><a-textarea v-model="step.desc" /></a-form-item><a-form-item label="预期结果"><a-textarea v-model="step.expected" /></a-form-item><div class="gc-two-col"><a-form-item label="动作"><a-select v-model="step.action" :options="actions" /></a-form-item><a-form-item label="操作对象"><a-input v-model="step.target" /></a-form-item><a-form-item label="测试数据"><a-input v-model="step.value" /></a-form-item><a-form-item label="定位器（有依据再填）"><a-input v-model="step.locator" /></a-form-item></div></div>
        <a-button v-if="job?.status !== 'saved'" long @click="editing.steps.push({seq:editing.steps.length+1,desc:'',expected:'',action:'click',target:'',value:'',locator:''})">添加步骤</a-button><a-form-item label="备注" class="gc-space-top"><a-textarea v-model="editing.remark" /></a-form-item>
      </a-form>
      <a-alert v-if="job?.status === 'saved'">正式用例请在用例管理中编辑。导出会读取最新的正式用例内容。</a-alert>
    </a-drawer>
    <a-modal v-model:visible="rulesVisible" title="规则定义" :width="820" @ok="applyRules"><a-textarea v-model="rulesJson" :auto-size="{minRows:16,maxRows:25}" :disabled="!canConfigure" /><p>JSON 数组；保留唯一 id，设置 field、type、value、level、enabled、message。</p></a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { useProjectStore } from '@/store/projectStore'
import request from '@/utils/request'

interface Step { seq:number; desc:string; expected:string; action:string; target:string; value:string; locator:string; [key:string]:unknown }
interface Case { tc_sno:string; tc_name:string; module_path:string; level:string; priority:string; test_type:string; judge_type:string; data_type:string; objective:string; automation:string; precondition:string; summary:string; sub_objective:string; remark:string; tags:string[]; source_images:string[]; source_references?:{unit_id:string;document:string;pages:number[];source_case_id:string}[]; steps:Step[]; [key:string]:unknown }
interface Rule { id:string; field:string; type:string; value:unknown; level:string; enabled:boolean; message:string; [key:string]:unknown }
interface Issues {errors:number; warnings:number; duplicates:number[]; detail:{case:string;issues:{level:string;message:string}[]}[]}
interface JobProgress {phase:string;batch_total:number;batch_done:number;resumable:boolean;last_error?:string;vision?:{image_total:number;image_done:number;batch_total:number;batch_done:number};modules:{id:string;path:string;description:string;batch_total:number;batch_done:number;case_count:number}[];coverage?:{assigned_units:number;cited_units:number}}
interface Job {id:string;title:string;status:string;stage:string;revision:number;cases:Case[];issues:Issues;notes:string[];saved_ids:number[];count:number;created_at:string;input?:{document_ids?:string[];text?:string};images?:{name:string}[];progress?:JobProgress}
interface Model {id:number;config_name:string;supports_vision:boolean;is_active:boolean}
interface Config {rules:Rule[];prompts:Record<string,string>;stages:Record<string,number|undefined>}
interface UploadLimits {text_chars:number;context_chars:number;documents:number;document_bytes:number;images:number;image_bytes:number;upload_bytes:number;vision_batch_size:number}
interface Boot {settings:Config;models:Model[];documents:{id:string;title:string}[];knowledge_bases:{id:string;name:string}[];jobs:Job[];can_configure:boolean;limits:UploadLimits}
const router = useRouter(), projectStore = useProjectStore(), projectId = computed(() => projectStore.currentProjectId)
const tab=ref('input'), error=ref(''), busy=ref(false), title=ref(''), requirement=ref(''), documentIds=ref<string[]>([]), docs=ref<File[]>([]), images=ref<File[]>([])
const textModelId=ref<number>(), visionModelId=ref<number>(), knowledgeBaseId=ref<string>(), includeDocumentImages=ref(false)
const models=ref<Model[]>([]), documents=ref<Boot['documents']>([]), knowledgeBases=ref<Boot['knowledge_bases']>([]), jobs=ref<Job[]>([]), job=ref<Job>(), config=ref<Config>({rules:[],prompts:{},stages:{}}), canConfigure=ref(false)
const caseSearch=ref(''), dirty=ref(false), editing=ref<Case>(), editorVisible=ref(false), rulesVisible=ref(false), rulesJson=ref('')
const imagePreviews=ref<{name:string;url:string}[]>([])
const MB=1024*1024
const limits=ref<UploadLimits>({text_chars:100000,context_chars:300000,documents:20,document_bytes:20*MB,images:50,image_bytes:8*MB,upload_bytes:80*MB,vision_batch_size:6})
const docPicker=ref<HTMLInputElement>(), imagePicker=ref<HTMLInputElement>(), fileError=ref(''), dragging=ref('')
const uploadBytes=computed(()=>[...docs.value,...images.value].reduce((sum,f)=>sum+f.size,0))
const fileKey=(f:File)=>`${f.name}:${f.size}:${f.lastModified}`
const formatSize=(bytes:number)=>bytes>=MB?`${(bytes/MB).toFixed(1)} MB`:`${Math.ceil(bytes/1024)} KB`
const journey=[{title:'准备素材',desc:'需求文字、文档与截图',tab:'input'},{title:'生成与校验',desc:'模型生成 · 规则检查',tab:'draft'},{title:'评审与入库',desc:'编辑草稿 · 确认保存',tab:'draft'},{title:'沉淀与复用',desc:'生成记录 · 多格式导出',tab:'history'}]
const stages=[{key:'vision',label:'图片解析',placeholder:'沿用当前激活的视觉模型'},{key:'text',label:'文字生成',placeholder:'沿用当前激活模型'},{key:'validate',label:'AI 评审（可选）',placeholder:'关闭，仅执行规则检查'},{key:'automation',label:'自动化建议（可选）',placeholder:'关闭，直接配置自动化执行器'}]
const prompts=[{key:'vision',label:'图片解析提示词'},{key:'vision_page',label:'逐页图片分析提示词'},{key:'text',label:'用例生成提示词'},{key:'validate',label:'AI 评审提示词'},{key:'automation',label:'自动化建议提示词'}]
const actions=['navigate','click','input','select','check','upload','hover','wait','assert','screenshot']
const testTypes=[{label:'功能',value:'functional'},{label:'冒烟',value:'smoke'},{label:'边界',value:'boundary'},{label:'异常',value:'exception'},{label:'权限',value:'permission'},{label:'安全',value:'security'},{label:'兼容',value:'compatibility'}]
const modelOptions=computed(()=>models.value.map(m=>({label:m.config_name+(m.is_active?' · 当前激活':''),value:m.id})))
const visionModelOptions=computed(()=>modelOptions.value.filter(m=>models.value.find(x=>x.id===m.value)?.supports_vision))
const documentOptions=computed(()=>documents.value.map(d=>({label:d.title,value:d.id})))
const knowledgeOptions=computed(()=>knowledgeBases.value.map(k=>({label:k.name,value:k.id})))
const running=computed(()=>['queued','running'].includes(job.value?.status||''))
const filteredCases=computed(()=>job.value?.cases.filter(c=>!caseSearch.value||`${c.tc_name} ${c.module_path}`.includes(caseSearch.value))||[])
const statusLabel=(s:string)=>({queued:'排队中',running:'处理中',draft:'待确认',saved:'已入库',failed:'失败',cancelled:'已取消'}[s]||s)
const statusColor=(s:string)=>({draft:'blue',saved:'green',failed:'red',running:'orange'}[s]||'gray')
const endpoint=(suffix='',pid=projectId.value)=>`/projects/${pid}/workbench/${suffix}`
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value))
async function api<T>(suffix:string,data?:unknown,pid=projectId.value):Promise<T>{const response=data===undefined?await request.get(endpoint(suffix,pid)):await request.post(endpoint(suffix,pid),data);return response.data.data as T}
async function guarded(fn:()=>Promise<void>){if(busy.value)return;busy.value=true;error.value='';try{await fn()}catch(e:unknown){error.value=(e as {error?:string;message?:string}).error||(e as Error).message||'操作失败，请稍后重试'}finally{busy.value=false}}
async function boot(){const pid=projectId.value;if(!pid)return;const data=await api<Boot>('',undefined,pid);if(pid!==projectId.value)return;config.value=data.settings;models.value=data.models;documents.value=data.documents;knowledgeBases.value=data.knowledge_bases;jobs.value=data.jobs;canConfigure.value=data.can_configure;if(data.limits)limits.value=data.limits}
function addFiles(files:File[], kind:'docs'|'images'){
  if(busy.value)return
  const list=kind==='docs'?docs.value:images.value, problems:string[]=[]
  let duplicateCount=0
  for(const f of files){
    if(list.some(existing=>fileKey(existing)===fileKey(f))){duplicateCount++;continue}
    const valid=kind==='docs'?/\.(txt|md|pdf|docx)$/i.test(f.name):/\.(png|jpe?g|webp)$/i.test(f.name)
    if(!valid){problems.push(`${f.name}：文件格式不支持`);continue}
    if(!f.size){problems.push(`${f.name}：文件为空`);continue}
    const maxBytes=kind==='docs'?limits.value.document_bytes:limits.value.image_bytes
    if(f.size>maxBytes){problems.push(`${f.name}：单个文件不能超过 ${maxBytes/MB} MB`);continue}
    const count=list.length+(kind==='docs'?documentIds.value.length:0), maxCount=kind==='docs'?limits.value.documents:limits.value.images
    if(count>=maxCount){problems.push(`${f.name}：最多选择 ${maxCount} ${kind==='docs'?'份文档（含已有文档）':'张图片'}`);continue}
    if(uploadBytes.value+f.size>limits.value.upload_bytes){problems.push(`${f.name}：待上传文件合计不能超过 ${limits.value.upload_bytes/MB} MB`);continue}
    list.push(f)
    if(kind==='images')imagePreviews.value.push({name:f.name,url:URL.createObjectURL(f)})
  }
  fileError.value=problems.join('；')
  if(duplicateCount)Message.info(`已跳过 ${duplicateCount} 个重复文件`)
}
function pickFiles(e:Event,kind:'docs'|'images'){const input=e.target as HTMLInputElement;addFiles(Array.from(input.files||[]),kind);input.value=''}
function dropFiles(e:DragEvent,kind:'docs'|'images'){dragging.value='';addFiles(Array.from(e.dataTransfer?.files||[]),kind)}
function removeImage(index:number){const preview=imagePreviews.value[index];if(preview)URL.revokeObjectURL(preview.url);images.value.splice(index,1);imagePreviews.value.splice(index,1)}
function clearImages(){imagePreviews.value.forEach(p=>URL.revokeObjectURL(p.url));images.value=[];imagePreviews.value=[]}
function restoreMaterials(){
  if(!job.value)return
  title.value=['图文生成用例','待 AI 命名的用例任务'].includes(job.value.title)?'':job.value.title
  requirement.value=job.value.input?.text||'';documentIds.value=[...(job.value.input?.document_ids||[])];docs.value=[]
  clearImages();includeDocumentImages.value=false
  if(job.value.images?.length)Message.info('已恢复文档与说明；如需继续使用图片，请重新选择图片或开启内嵌图片分析。')
  tab.value='input';error.value='';fileError.value=''
}
function startNew(){job.value=undefined;dirty.value=false;caseSearch.value='';tab.value='input';error.value=''}
function fillExample(){title.value='登录与权限验收';requirement.value='被测系统：本地 SGUI，http://127.0.0.1:8778/login。\nR1：打开登录页后直接显示用户名和密码输入框及“进入工作台”按钮。\nR2：正确账号与密码可登录，进入工作台。\nR3：错误密码应显示失败提示，不创建登录状态。\nR4：用户名或密码为空时阻止提交并提示必填。\nR5：退出登录后访问需要认证的页面应跳转登录页。\n请生成 5 条测试用例，覆盖以上规则。测试数据使用占位名称，不包含真实密码。'}
async function generate(){await guarded(async()=>{
  if(!requirement.value.trim()&&!docs.value.length&&!documentIds.value.length&&!images.value.length)throw new Error('请上传需求文档、引用已有文档，或输入需求说明。图片也可单独作为材料。')
  if(docs.value.length+documentIds.value.length>limits.value.documents)throw new Error(`本次最多关联 ${limits.value.documents} 份文档，请移除部分文件或已有文档。`)
  if(images.value.length>limits.value.images)throw new Error(`本次最多上传 ${limits.value.images} 张图片，请移除部分图片。`)
  if(uploadBytes.value>limits.value.upload_bytes)throw new Error(`本次上传文件合计不能超过 ${limits.value.upload_bytes/MB} MB。`)
  const pid=projectId.value, form=new FormData()
  form.append('title',title.value.trim());form.append('text',requirement.value.trim());form.append('document_ids',JSON.stringify(documentIds.value));form.append('include_document_images',String(includeDocumentImages.value))
  if(textModelId.value)form.append('text_model_id',String(textModelId.value));if(visionModelId.value)form.append('vision_model_id',String(visionModelId.value));if(knowledgeBaseId.value)form.append('knowledge_base_id',knowledgeBaseId.value)
  docs.value.forEach(f=>form.append('docs',f));images.value.forEach(f=>form.append('images',f))
  const result=await api<Job>('generate/',form,pid);if(pid!==projectId.value)return
  job.value=result;dirty.value=false;tab.value='draft'
  if(result.input?.document_ids){documentIds.value=result.input.document_ids;docs.value=[]}
  await boot()
})}
async function openJob(id:string){await guarded(async()=>{const pid=projectId.value;const data=await api<Job>(`jobs/${id}/`,undefined,pid);if(pid!==projectId.value)return;job.value=data;dirty.value=false;tab.value='draft'})}
async function loadPreset(){await guarded(async()=>{const pid=projectId.value;const result=await api<Job>('preset/',{},pid);if(pid!==projectId.value)return;job.value=result;dirty.value=false;tab.value='draft';await boot()})}
async function importJson(e:Event){const input=e.target as HTMLInputElement;const file=input.files?.[0];if(!file)return;await guarded(async()=>{if(file.size>10*1024*1024)throw new Error('JSON 文件最多 10 MB');const pid=projectId.value;const result=await api<Job>('import/',{payload:JSON.parse(await file.text()),title:file.name},pid);if(pid!==projectId.value)return;job.value=result;dirty.value=false;tab.value='draft';await boot()});input.value=''}
async function persistDraft(){if(!job.value||job.value.status!=='draft')return;const pid=projectId.value,id=job.value.id;const result=await api<Job>(`jobs/${id}/draft/`,{cases:job.value.cases,revision:job.value.revision},pid);if(pid!==projectId.value||id!==job.value?.id)return;job.value=result;dirty.value=false}
async function saveDraft(){await guarded(async()=>{await persistDraft();Message.success('草稿已保存并完成规则校验')})}
async function commit(){await guarded(async()=>{const originalProject=projectId.value,originalJob=job.value?.id;await persistDraft();if(!job.value||projectId.value!==originalProject||job.value.id!==originalJob)return;if(job.value.issues.errors)throw new Error('请先修正规则错误，可点击“查看规则问题”定位。');const pid=projectId.value,id=job.value.id;const result=await api<Job>(`jobs/${id}/commit/`,{revision:job.value.revision},pid);if(pid!==projectId.value)return;job.value=result;await boot();Message.success('已存入 SGUI 用例管理')})}
async function cancelJob(){await guarded(async()=>{if(!job.value)return;const pid=projectId.value,id=job.value.id;const result=await api<Job>(`jobs/${id}/cancel/`,{},pid);if(pid===projectId.value&&id===job.value?.id)job.value=result})}
async function resumeJob(){await guarded(async()=>{if(!job.value)return;const pid=projectId.value,id=job.value.id;const result=await api<Job>(`jobs/${id}/resume/`,{},pid);if(pid===projectId.value&&id===job.value?.id){job.value=result;await boot();Message.success('已提交，将复用已完成进度')}})}
async function download(value:string|number|Record<string,unknown>|undefined){await guarded(async()=>{if(dirty.value)await persistDraft();if(!job.value)return;const fmt=String(value);const response=await request.get(endpoint(`jobs/${job.value.id}/export/?file_type=${fmt}`),{responseType:'blob'});const url=URL.createObjectURL(response.data.data);const a=document.createElement('a');a.href=url;a.download=`cases-${job.value.id.slice(0,8)}.${fmt}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)})}
function editCase(id:string){const c=job.value?.cases.find(c=>c.tc_sno===id);if(c){editing.value=clone(c);editorVisible.value=true}}
function syncPriority(){if(editing.value)editing.value.priority=({P0:'高',P1:'中',P2:'低',P3:'低'} as Record<string,string>)[editing.value.level]||'中'}
function applyEdit(){if(!job.value||!editing.value||job.value.status!=='draft')return;const index=job.value.cases.findIndex(c=>c.tc_sno===editing.value?.tc_sno);editing.value.steps.forEach((s,i)=>s.seq=i+1);job.value.cases[index]=clone(editing.value);dirty.value=true;editorVisible.value=false}
function openRulesEditor(){rulesJson.value=JSON.stringify(config.value.rules,null,2);rulesVisible.value=true}
function applyRules(){try{const rules=JSON.parse(rulesJson.value);if(!Array.isArray(rules))throw new Error();config.value.rules=rules;Message.info('规则定义已修改，请保存工作台设置')}catch{error.value='规则 JSON 格式错误，请重新编辑。'}}
async function saveSettings(){await guarded(async()=>{await api('settings/',config.value);Message.success('工作台设置已保存')})}
let poll:ReturnType<typeof setTimeout>|undefined, disposed=false
async function pollJob(){if(disposed)return;try{const pid=projectId.value,id=job.value?.id;if(running.value&&id){const next=await api<Job>(`jobs/${id}/`,undefined,pid);if(pid===projectId.value&&id===job.value?.id){job.value=next;jobs.value=jobs.value.map(item=>item.id===id?{...item,title:next.title,status:next.status,count:next.count}:item);if(!running.value)await boot()}}}catch(e){error.value=(e as {error?:string}).error||'任务状态暂时无法读取，稍后会重试'}finally{if(!disposed)poll=setTimeout(pollJob,2500)}}
watch(projectId,()=>{job.value=undefined;dirty.value=false;documentIds.value=[];knowledgeBaseId.value=undefined;docs.value=[];images.value=[];imagePreviews.value.forEach(p=>URL.revokeObjectURL(p.url));imagePreviews.value=[];config.value={rules:[],prompts:{},stages:{}};models.value=[];documents.value=[];knowledgeBases.value=[];jobs.value=[];textModelId.value=undefined;visionModelId.value=undefined;void boot().catch(e=>{error.value=e.error||'项目数据加载失败'})},{immediate:true})
void pollJob()
onUnmounted(()=>{disposed=true;clearTimeout(poll);imagePreviews.value.forEach(p=>URL.revokeObjectURL(p.url))})
</script>

<style scoped>
.gc-workbench {
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  overflow: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.gc-workbench:focus-visible {
  outline: 2px solid var(--color-primary-6, #1677ff);
  outline-offset: -2px;
}
.gc-hidden-picker { display: none; }
.gc-upload-tools { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.gc-upload-tools :deep(button:not(:disabled)) { cursor: pointer; }
.gc-upload.gc-drop-active { border-color: #1677ff; background: var(--color-primary-light-1); }
.gc-file-list { list-style: none; padding: 0; margin: 14px 0 0; }
.gc-file-list li { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 0; border-top: 1px solid var(--color-border-2); }
.gc-file-list li > div { min-width: 0; overflow-wrap: anywhere; }
.gc-file-list small { display: block; color: var(--color-text-3); margin-top: 4px; }
.gc-file-list :deep(button) { flex-shrink: 0; }
.gc-material-summary { color: var(--color-text-3); font-size: 12px; line-height: 1.8; margin: 0 0 16px; }
.gc-images { max-height: 360px; overflow-y: auto; scrollbar-gutter: stable; }
.gc-module-progress { display: flex; justify-content: space-between; align-items: start; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--color-border-2); overflow-wrap: anywhere; }
.gc-module-progress > div { min-width: 0; flex: 1; }
.gc-module-progress > :last-child { flex-shrink: 0; }
.gc-module-progress p { font-size: 12px; color: var(--color-text-3); margin: 6px 0; }
.gc-workbench{max-width:1680px;margin:0 auto;color:var(--color-text-1)}.gc-heading{display:flex;justify-content:space-between;gap:20px;align-items:center;margin:5px 0 22px}.gc-eyebrow{color:#6b7d96;font-size:12px;letter-spacing:1px}.gc-heading h1{font-size:24px;line-height:1.5;margin:5px 0;font-weight:650}.gc-heading p,.gc-result-heading p,.gc-import-row p{margin:4px 0;color:var(--color-text-3);font-size:13px}.gc-grid{display:grid;grid-template-columns:226px minmax(0,1fr);gap:16px;align-items:start}.gc-guide,.gc-panel{border:1px solid var(--color-border-2);border-radius:5px;background:var(--color-bg-2)}.gc-guide{padding:18px 12px}.gc-guide-title{font-weight:650;padding:0 10px 14px;border-bottom:1px solid var(--color-border-2);margin-bottom:14px;word-break:break-all}.gc-journey{display:flex;gap:11px;align-items:center;border:0;background:transparent;width:100%;text-align:left;padding:14px 8px;color:var(--color-text-2);cursor:pointer;border-radius:4px}.gc-journey>span{border:1px solid #ccd8e7;width:27px;height:27px;border-radius:50%;display:grid;place-items:center;font-size:12px;flex:none}.gc-journey strong{font-size:13px}.gc-journey small{display:block;color:#8896aa;font-size:11px;margin-top:5px}.gc-journey.active{background:#eef5ff;color:#1668d6}.gc-journey.active>span{background:#1677ff;color:#fff;border-color:#1677ff}.gc-guide-divider{height:1px;background:var(--color-border-2);margin:18px 8px}.gc-guide-caption{font-size:11px;color:#8190a4;padding:0 10px 10px}.gc-guide>a{display:flex;gap:9px;align-items:center;color:var(--color-text-2);font-size:12px;padding:11px 10px;text-decoration:none}.gc-guide>a:hover{color:#1677ff;background:#eef5ff}.gc-guide-tip{font-size:11px;line-height:1.8;color:#8a98aa;padding:18px 10px 2px}.gc-panel{min-width:0}.gc-panel :deep(.arco-tabs-nav){padding:0 20px}.gc-pane{padding:8px 24px 24px}.gc-two-col{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}.gc-upload{background:var(--color-fill-1);border:1px dashed #b8c9e0;border-radius:4px;padding:18px;margin-bottom:20px;min-width:0}.gc-upload strong{font-size:13px}.gc-upload p{color:#8391a5;font-size:12px}.gc-upload input{max-width:100%;font-size:12px}.gc-file{padding-top:8px;color:#58708f;font-size:12px;overflow-wrap:anywhere}.gc-images{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}.gc-images>div{width:100px}.gc-images img{width:100px;height:68px;object-fit:cover;border:1px solid #dae4ef;border-radius:3px}.gc-images small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.gc-actions{display:flex;align-items:center;justify-content:space-between;border-top:1px solid var(--color-border-2);padding-top:20px;gap:12px}.gc-actions>span{font-size:12px;color:#8291a6}.gc-space-top{margin-top:20px}.gc-space-bottom{margin-bottom:16px}.gc-error{margin-bottom:16px}.gc-result-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}.gc-pane h2{font-size:17px;margin:2px 0 8px}.gc-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}.gc-metrics>div{background:var(--color-fill-1);border:1px solid var(--color-border-2);border-radius:4px;padding:15px 18px;display:flex;align-items:baseline;gap:9px}.gc-metrics b{font-size:25px;color:#216fd1}.gc-metrics span{font-size:12px;color:#7a8ba2}.gc-result-tools,.gc-import-row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:20px 0;flex-wrap:wrap}.gc-subtext{display:block;color:#8c9bae;font-size:10px;margin-top:5px}.gc-note{font-family:inherit;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;color:var(--color-text-2);font-size:13px}.gc-issue{padding:10px 0;border-bottom:1px solid var(--color-border-2);font-size:12px}.gc-issue>div{margin-top:8px}.gc-next{background:var(--color-fill-1);padding:18px;border:1px solid var(--color-border-2);margin-top:20px;font-size:13px;line-height:1.8}.gc-file-button{background:#1677ff;color:white;padding:7px 13px;border-radius:4px;cursor:pointer;font-size:14px}.gc-file-button input{display:none}.gc-step{border:1px solid var(--color-border-2);padding:16px;margin-bottom:16px;border-radius:4px}.gc-step-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}@media(max-width:1150px){.gc-grid{grid-template-columns:1fr}.gc-guide{display:none}.gc-heading{align-items:flex-start}.gc-metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:800px){.gc-two-col{grid-template-columns:1fr}.gc-heading{flex-direction:column}.gc-pane{padding:8px 14px 18px}.gc-actions{align-items:flex-start;flex-direction:column}}
</style>
