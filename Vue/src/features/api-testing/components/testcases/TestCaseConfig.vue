<script setup lang="ts">
import { computed } from 'vue'
import {
  IconFile,
  IconSettings,
  IconLink,
} from '@arco-design/web-vue/es/icon'

interface TestCaseConfigData {
  verify?: boolean
  base_url: string
}

interface BasicInfoData {
  description: string
  config: TestCaseConfigData
}

interface Props {
  modelValue: BasicInfoData
  readonly?: boolean
}

const props = defineProps<Props>()
const emit = defineEmits(['update:modelValue'])

const updateValue = (key: keyof BasicInfoData, value: any) => {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

const updateConfig = (key: keyof TestCaseConfigData, value: any) => {
  emit('update:modelValue', {
    ...props.modelValue,
    config: {
      ...props.modelValue.config,
      [key]: value
    }
  })
}

const sslVerifyMode = computed(() => {
  if (props.modelValue.config.verify === true) return 'on'
  if (props.modelValue.config.verify === false) return 'off'
  return 'inherit'
})

const updateSslVerifyMode = (value: string | number | boolean) => {
  const nextConfig = { ...props.modelValue.config }
  if (value === 'on') {
    nextConfig.verify = true
  } else if (value === 'off') {
    nextConfig.verify = false
  } else {
    delete nextConfig.verify
  }

  emit('update:modelValue', {
    ...props.modelValue,
    config: nextConfig
  })
}
</script>

<template>
  <div class="grid grid-cols-3 gap-6">
    <a-card class="col-span-2 config-card">
      <template #title>
        <div class="flex items-center gap-2 card-title">
          <icon-file />
          <span>用例描述</span>
        </div>
      </template>
      <a-textarea
        :model-value="modelValue.description"
        @update:model-value="val => updateValue('description', val)"
        placeholder="请输入用例描述"
        :auto-size="{ minRows: 3, maxRows: 6 }"
      />
    </a-card>
    <a-card class="config-card">
      <template #title>
        <div class="flex items-center gap-2 card-title">
          <icon-settings />
          <span>用例配置</span>
        </div>
      </template>
      <div class="space-y-4">
        <div class="flex justify-between items-center">
          <span class="label-text">SSL验证</span>
          <a-radio-group
            type="button"
            size="small"
            :model-value="sslVerifyMode"
            @update:model-value="updateSslVerifyMode"
          >
            <a-radio value="inherit">继承</a-radio>
            <a-radio value="on">开启</a-radio>
            <a-radio value="off">关闭</a-radio>
          </a-radio-group>
        </div>
        <a-input
          :model-value="modelValue.config.base_url"
          @update:model-value="val => updateConfig('base_url', val)"
          placeholder="请输入基础URL"
        >
          <template #prefix>
            <icon-link />
          </template>
        </a-input>
      </div>
    </a-card>
  </div>
</template>

<style lang="postcss" scoped>
@reference "tailwindcss";
:deep(.arco-input-wrapper) {
  @apply bg-gray-900/60 border-gray-700;

  input {
    @apply text-gray-200;
    &::placeholder {
      @apply text-gray-500;
    }
  }
}

:deep(.arco-textarea-wrapper) {
  @apply bg-gray-900/60 border-gray-700;

  textarea {
    @apply text-gray-200;
    &::placeholder {
      @apply text-gray-500;
    }
  }
}
</style>
