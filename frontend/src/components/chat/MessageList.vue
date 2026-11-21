<script setup lang="ts">
import { onMounted, useTemplateRef, watch } from 'vue'
import type { ChatMessage } from '../../types/chat'
import AssistantMarkdown from './AssistantMarkdown.vue'

const props = defineProps<{ messages: ChatMessage[] }>()
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
watch(() => props.messages, scrollToBottom, { flush: 'post' })
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
    <p v-if="!messages.length" class="text-sm text-slate-600 dark:text-slate-400">
      No messages yet. Send a message to start the conversation.
    </p>
    <ol v-else class="space-y-3">
      <li
        v-for="message in messages"
        :key="message.key"
        class="rounded-md p-3 text-sm"
        :class="
          message.role === 'user' ? 'bg-blue-50 dark:bg-blue-950' : 'bg-slate-100 dark:bg-slate-800'
        "
      >
        <p class="mb-1 font-semibold">{{ message.role === 'user' ? 'You' : 'Assistant' }}</p>
        <p v-if="message.role === 'user'" class="whitespace-pre-wrap break-words">
          {{ message.text }}
        </p>
        <AssistantMarkdown v-else-if="message.text" :text="message.text" />
        <p v-else>Waiting for a response...</p>
      </li>
    </ol>
  </div>
</template>
