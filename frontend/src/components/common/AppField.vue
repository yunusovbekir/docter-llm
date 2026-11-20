<script setup lang="ts">
import { computed, useId } from 'vue'

const props = defineProps<{ id?: string; label: string; error?: string; hint?: string }>()

const generatedId = useId()
const id = computed(() => props.id ?? generatedId)
const hintId = computed(() => `${id.value}-hint`)
const errorId = computed(() => `${id.value}-error`)
const descriptions = computed(() =>
  [props.error && errorId.value, props.hint && hintId.value].filter(Boolean).join(' '),
)
const controlAttrs = computed(() => ({
  id: id.value,
  ...(props.error && { 'aria-invalid': true }),
  ...(descriptions.value && { 'aria-describedby': descriptions.value }),
  class: [
    'block w-full rounded-md border bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-400 dark:focus-visible:outline-blue-400',
    props.error ? 'border-red-600 dark:border-red-400' : 'border-slate-400 dark:border-slate-500',
  ],
}))
</script>

<template>
  <div class="space-y-1">
    <label :for="id" class="block text-sm font-medium text-slate-900 dark:text-slate-100">
      {{ label }}
    </label>
    <slot :control-attrs="controlAttrs" />
    <p v-if="error" :id="errorId" class="text-sm text-red-700 dark:text-red-300">{{ error }}</p>
    <p v-if="hint" :id="hintId" class="text-sm text-slate-600 dark:text-slate-400">{{ hint }}</p>
  </div>
</template>
