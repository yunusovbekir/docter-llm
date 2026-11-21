<script setup lang="ts">
import type { components } from '../../api/schema'
import { useChat } from '../../composables/useChat'
import ChatInput from './ChatInput.vue'
import MessageList from './MessageList.vue'

const props = defineProps<{ session: components['schemas']['SessionDetail'] }>()
const { messages, isStreaming, send } = useChat(props.session)
</script>

<template>
  <section aria-label="Chat" class="space-y-4">
    <MessageList :messages="messages" />
    <ChatInput :busy="isStreaming" @send="send" />
  </section>
</template>
