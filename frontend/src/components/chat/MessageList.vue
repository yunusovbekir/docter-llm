<script setup lang="ts">
import { onMounted, onUpdated, useTemplateRef, watch } from 'vue'
import type { ChatEntry } from '../../types/chat'
import AssistantMarkdown from './AssistantMarkdown.vue'
import ToolCallCard from './ToolCallCard.vue'

const props = defineProps<{ entries: ChatEntry[]; waiting: boolean }>()
const viewport = useTemplateRef('viewport')
let follow = true

function updateFollow() {
  const element = viewport.value
  if (element) follow = element.scrollHeight - element.clientHeight - element.scrollTop <= 16
}

function scrollToBottom() {
  const element = viewport.value
  if (follow && element) element.scrollTop = element.scrollHeight
}

onMounted(scrollToBottom)
onUpdated(scrollToBottom)
watch(
  () => props.entries.map((entry) => (entry.role === 'tool' ? entry.outcome : undefined)),
  scrollToBottom,
  { flush: 'post' },
)
</script>

<template>
  <div
    ref="viewport"
    role="log"
    aria-label="Conversation"
    tabindex="0"
    class="max-h-[55vh] overflow-y-auto rounded-md border border-slate-200 p-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 dark:border-slate-800 dark:focus-visible:outline-blue-400"
    @scroll="updateFollow"
    @load.capture="scrollToBottom"
  >
    <p v-if="!entries.length" class="text-sm text-slate-600 dark:text-slate-400">
      No messages yet. Send a message to start the conversation.
    </p>
    <ol v-else class="space-y-3">
      <li v-for="message in entries" :key="message.key">
        <ToolCallCard v-if="message.role === 'tool'" :call="message" />
        <p
          v-else-if="message.role === 'warning' || message.role === 'error'"
          :role="message.role === 'error' ? 'alert' : 'status'"
          class="break-words rounded-md border p-3 text-sm"
          :class="
            message.role === 'error'
              ? 'border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-200'
              : 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200'
          "
        >
          <strong>{{ message.role === 'error' ? 'Error:' : 'Warning:' }}</strong> {{ message.text }}
        </p>
        <div
          v-else
          class="rounded-md p-3 text-sm"
          :class="
            message.role === 'user'
              ? 'bg-blue-50 dark:bg-blue-950'
              : 'bg-slate-100 dark:bg-slate-800'
          "
        >
          <p class="mb-1 font-semibold">{{ message.role === 'user' ? 'You' : 'Assistant' }}</p>
          <p v-if="message.role === 'user'" class="whitespace-pre-wrap break-words">
            {{ message.text }}
          </p>
          <AssistantMarkdown v-else :text="message.text" />
        </div>
      </li>
      <li v-if="waiting" class="rounded-md bg-slate-100 p-3 text-sm dark:bg-slate-800">
        Waiting for a response...
      </li>
    </ol>
  </div>
</template>
