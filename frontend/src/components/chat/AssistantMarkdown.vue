<script lang="ts">
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({ html: false })
</script>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ text: string }>()
const html = computed(() =>
  DOMPurify.sanitize(markdown.render(props.text), {
    USE_PROFILES: { html: true },
  }),
)
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- Markdown output is sanitized by DOMPurify. -->
  <div class="assistant-markdown break-words" v-html="html"></div>
</template>

<style scoped>
.assistant-markdown :deep(p + p) {
  margin-top: 0.5rem;
}
.assistant-markdown :deep(a) {
  @apply text-blue-700 underline dark:text-blue-300;
}
.assistant-markdown :deep(pre) {
  @apply overflow-x-auto rounded bg-slate-200 p-2 dark:bg-slate-900;
}
.assistant-markdown :deep(ul) {
  @apply list-disc pl-5;
}
.assistant-markdown :deep(ol) {
  @apply list-decimal pl-5;
}
.assistant-markdown :deep(h1),
.assistant-markdown :deep(h2),
.assistant-markdown :deep(h3) {
  @apply font-semibold;
}
</style>
