<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { components } from '../../api/schema'
import AppButton from '../common/AppButton.vue'
import ChangeStatusBadge from './ChangeStatusBadge.vue'

type Hunk = components['schemas']['ChangeHunkRead']

const props = defineProps<{ hunks: Hunk[]; disabled: boolean; canJump: boolean }>()
const emit = defineEmits<{
  approve: [indices: number[]]
  deny: [indices: number[]]
  jump: [line: number]
}>()
const checked = ref<number[]>([])
const pending = computed(() => props.hunks.filter(({ status }) => status === 'pending'))
const selected = computed(() =>
  pending.value.map(({ index }) => index).filter((index) => checked.value.includes(index)),
)
watch(pending, () => {
  checked.value = selected.value
})

function proposedLine(header: string) {
  const match = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/.exec(header)
  if (!match) return undefined
  // An empty range starts at the gap after its numbered line.
  return Math.max(1, Number(match[1]) + (match[2] === '0' ? 1 : 0))
}

const rows = computed(() => props.hunks.map((hunk) => ({ hunk, line: proposedLine(hunk.header) })))
</script>

<template>
  <fieldset v-if="rows.length" class="space-y-2">
    <legend class="text-sm font-semibold">Hunks</legend>
    <ul class="space-y-1">
      <li v-for="{ hunk, line } in rows" :key="hunk.id" class="flex flex-wrap items-center gap-2">
        <input
          v-model="checked"
          type="checkbox"
          :value="hunk.index"
          :disabled="disabled || hunk.status !== 'pending'"
          :aria-label="`Select hunk ${hunk.index + 1}`"
          class="size-4 shrink-0 accent-blue-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:opacity-50 dark:accent-blue-400 dark:focus-visible:outline-blue-400"
        />
        <button
          type="button"
          :disabled="!canJump || line === undefined"
          class="min-w-0 break-all rounded text-left font-mono text-sm text-blue-700 enabled:hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 disabled:opacity-60 dark:text-blue-300 dark:focus-visible:outline-blue-400"
          @click="line !== undefined && emit('jump', line)"
        >
          {{ hunk.header }}
        </button>
        <ChangeStatusBadge :status="hunk.status" />
      </li>
    </ul>
    <div class="flex flex-wrap gap-2">
      <AppButton :disabled="disabled || !selected.length" @click="emit('approve', selected)">
        Approve selected
      </AppButton>
      <AppButton
        variant="danger"
        :disabled="disabled || !selected.length"
        @click="emit('deny', selected)"
      >
        Deny selected
      </AppButton>
    </div>
  </fieldset>
</template>
