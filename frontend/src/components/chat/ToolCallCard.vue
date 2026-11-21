<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import type { ChatToolCall } from '../../types/chat'
import AppBadge from '../common/AppBadge.vue'

const props = defineProps<{ call: ChatToolCall }>()
const limit = 2_000
const full = ref(false)
const contentId = useId()
const status = computed(() => {
  if (!props.call.outcome) return { label: 'Running', tone: 'info' } as const
  if (props.call.outcome.isError) return { label: 'Error', tone: 'danger' } as const
  return { label: 'OK', tone: 'success' } as const
})
const argumentsText = computed(() => JSON.stringify(props.call.arguments, null, 2) ?? '')
const resultText = computed(() => {
  const value = props.call.outcome?.result
  return typeof value === 'string' ? value : (JSON.stringify(value, null, 2) ?? '')
})
const truncated = computed(
  () => argumentsText.value.length > limit || resultText.value.length > limit,
)
const preview = (text: string) => (full.value ? text : text.slice(0, limit))
</script>

<template>
  <details class="rounded-md border border-slate-200 text-sm dark:border-slate-700">
    <summary
      class="cursor-pointer break-words rounded-md p-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 dark:focus-visible:outline-blue-400"
    >
      <span class="mr-2 font-semibold">{{ call.name + ' ' }}</span>
      <AppBadge :tone="status.tone">{{ status.label }}</AppBadge>
    </summary>
    <div class="space-y-2 px-3 pb-3">
      <div :id="contentId" class="space-y-2">
        <p class="font-medium">Arguments</p>
        <pre class="whitespace-pre-wrap break-all rounded bg-slate-100 p-2 dark:bg-slate-800">{{
          preview(argumentsText)
        }}</pre>
        <template v-if="call.outcome">
          <p class="font-medium">Result</p>
          <pre class="whitespace-pre-wrap break-all rounded bg-slate-100 p-2 dark:bg-slate-800">{{
            preview(resultText)
          }}</pre>
        </template>
      </div>
      <button
        v-if="truncated"
        type="button"
        :aria-expanded="full"
        :aria-controls="contentId"
        class="rounded text-blue-700 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 dark:text-blue-300 dark:focus-visible:outline-blue-400"
        @click="full = !full"
      >
        {{ full ? 'Show less' : 'Show more' }}
      </button>
    </div>
  </details>
</template>
