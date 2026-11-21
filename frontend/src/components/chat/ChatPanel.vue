<script setup lang="ts">
import type { components } from '../../api/schema'
import { useChat } from '../../composables/useChat'
import ChatInput from './ChatInput.vue'
import MessageList from './MessageList.vue'

const props = defineProps<{ session: components['schemas']['SessionDetail'] }>()
const { entries, waiting, runActive, busy, stopping, send, stop } = useChat(props.session)
</script>

<template>
  <section aria-label="Chat" class="space-y-4">
    <MessageList :entries="entries" :waiting="waiting" />
    <ChatInput
      :busy="busy"
      :can-stop="runActive || stopping"
      :stopping="stopping"
      @send="send"
      @stop="stop"
    />
  </section>
</template>
