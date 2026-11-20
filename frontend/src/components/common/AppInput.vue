<script setup lang="ts">
import { mergeProps, ref, watch } from 'vue'
import AppField from './AppField.vue'

defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    id?: string
    label: string
    error?: string
    hint?: string
    type?: 'text' | 'email' | 'password' | 'search' | 'tel' | 'url' | 'number'
    disabled?: boolean
  }>(),
  { type: 'text', id: undefined, error: undefined, hint: undefined },
)
const model = defineModel<string>({ required: true })
const showPassword = ref(false)

watch(
  () => props.type,
  () => {
    showPassword.value = false
  },
)
</script>

<template>
  <AppField :id="id" v-slot="{ controlAttrs }" :label="label" :error="error" :hint="hint">
    <div class="flex items-start gap-2">
      <input
        v-bind="mergeProps($attrs, controlAttrs)"
        :type="showPassword ? 'text' : type"
        :disabled="disabled"
        :value="model"
        class="min-w-0"
        @input="model = ($event.target as HTMLInputElement).value"
      />
      <button
        v-if="type === 'password'"
        type="button"
        :disabled="disabled"
        :aria-controls="controlAttrs.id"
        :aria-label="`${showPassword ? 'Hide' : 'Show'} ${label}`"
        class="min-w-14 shrink-0 rounded-md px-2 py-2 text-sm font-medium text-blue-700 enabled:hover:bg-blue-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:cursor-not-allowed disabled:opacity-60 dark:text-blue-300 dark:enabled:hover:bg-slate-800 dark:focus-visible:outline-blue-400"
        @click="showPassword = !showPassword"
      >
        {{ showPassword ? 'Hide' : 'Show' }}
      </button>
    </div>
  </AppField>
</template>
