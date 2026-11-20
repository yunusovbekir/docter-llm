<script setup lang="ts">
import { mergeProps } from 'vue'
import AppField from './AppField.vue'

defineOptions({ inheritAttrs: false })

defineProps<{
  id?: string
  label: string
  options: readonly { value: string; label: string; disabled?: boolean }[]
  error?: string
  hint?: string
  disabled?: boolean
}>()
const model = defineModel<string>({ required: true })
</script>

<template>
  <AppField :id="id" v-slot="{ controlAttrs }" :label="label" :error="error" :hint="hint">
    <select v-bind="mergeProps($attrs, controlAttrs)" v-model="model" :disabled="disabled">
      <option
        v-for="option in options"
        :key="option.value"
        :value="option.value"
        :disabled="option.disabled"
      >
        {{ option.label }}
      </option>
    </select>
  </AppField>
</template>
