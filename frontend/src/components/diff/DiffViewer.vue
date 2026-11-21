<script setup lang="ts">
import { VueMonacoDiffEditor } from '@guolao/vue-monaco-editor'
import type { editor } from 'monaco-editor'
import { computed, onMounted, onUnmounted, ref, useId } from 'vue'
import type { components } from '../../api/schema'
import { useToast } from '../../stores/toast'
import AppSpinner from '../common/AppSpinner.vue'
import { languageForPath } from './monaco'

const props = defineProps<{ change: components['schemas']['ChangeRead'] }>()
const heading = useId()
const layoutId = useId()
const toast = useToast()
const preferenceKey = 'docter.diff-layout'
const layout = ref<'side-by-side' | 'inline'>('side-by-side')
const root = globalThis.document.documentElement
const dark = ref(root.classList.contains('dark'))
let diffEditor: editor.IStandaloneDiffEditor | undefined
let queuedLine: number | undefined

function scrollToLine(line: number) {
  const modified = diffEditor?.getModifiedEditor()
  const model = modified?.getModel()
  if (!modified || !model) {
    queuedLine = line
    return
  }
  modified.revealLineInCenter(Math.min(line, model.getLineCount()))
}

function mountEditor(instance: editor.IStandaloneDiffEditor) {
  diffEditor = instance
  if (queuedLine !== undefined) {
    const line = queuedLine
    queuedLine = undefined
    scrollToLine(line)
  }
}

defineExpose({ scrollToLine })
const observer = new globalThis.MutationObserver(() => {
  dark.value = root.classList.contains('dark')
})

onMounted(() => {
  observer.observe(root, { attributes: true, attributeFilter: ['class'] })
  try {
    if (globalThis.localStorage.getItem(preferenceKey) === 'inline') layout.value = 'inline'
  } catch {
    toast.warning('The saved diff layout could not be read.')
  }
})
onUnmounted(() => observer.disconnect())

function setLayout(event: globalThis.Event) {
  const value = (event.target as globalThis.HTMLSelectElement).value
  layout.value = value === 'inline' ? 'inline' : 'side-by-side'
  try {
    globalThis.localStorage.setItem(preferenceKey, layout.value)
  } catch {
    toast.warning('The diff layout preference could not be saved.')
  }
}

const language = computed(() => languageForPath(props.change.file_path))
const options = computed(() => ({
  readOnly: true,
  originalEditable: false,
  renderSideBySide: layout.value === 'side-by-side',
  useInlineViewWhenSpaceIsLimited: false,
  automaticLayout: true,
  accessibilitySupport: 'auto' as const,
  originalAriaLabel: 'Original content',
  modifiedAriaLabel: 'Proposed content',
}))
</script>

<template>
  <section :aria-labelledby="heading" class="min-w-0 space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 :id="heading" class="break-all font-semibold">Diff: {{ change.file_path }}</h2>
      <div class="flex items-center gap-2 text-sm">
        <label :for="layoutId">Diff layout</label>
        <select
          :id="layoutId"
          :value="layout"
          class="rounded-md border border-slate-400 bg-white px-2 py-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 dark:border-slate-600 dark:bg-slate-900 dark:focus-visible:outline-blue-400"
          @change="setLayout"
        >
          <option value="side-by-side">Side by side</option>
          <option value="inline">Inline</option>
        </select>
      </div>
    </div>
    <div class="overflow-hidden rounded-md border border-slate-200 dark:border-slate-800">
      <VueMonacoDiffEditor
        height="28rem"
        :original="change.original_content"
        :modified="change.proposed_content"
        :language="language"
        :theme="dark ? 'vs-dark' : 'vs'"
        :options="options"
        @mount="mountEditor"
      >
        <template #loading><AppSpinner label="Loading diff editor" /></template>
      </VueMonacoDiffEditor>
    </div>
  </section>
</template>
