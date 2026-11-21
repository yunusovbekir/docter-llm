<script setup lang="ts">
import type { components } from '../../api/schema'
import { useChat } from '../../composables/useChat'
import { useCoverageHandoff } from '../../composables/useCoverageHandoff'
import { useSession } from '../../stores/session'
import ChatInput from './ChatInput.vue'
import MessageList from './MessageList.vue'

const props = defineProps<{ session: components['schemas']['SessionDetail'] }>()
const emit = defineEmits<{
  changeProposed: [sessionId: components['schemas']['SessionDetail']['id']]
}>()
const session = props.session
const initialDraft = useSession().takeHandoffDraft(session.id)
const { handingOff, handoff } = useCoverageHandoff(session.project_id)
const { entries, waiting, runActive, busy, stopping, send, revise, stop } = useChat(session, () => {
  emit('changeProposed', session.id)
})
defineExpose({ busy, revise })
</script>

<template>
  <section aria-label="Chat" class="space-y-4">
    <MessageList
      :entries="entries"
      :waiting="waiting"
      :handing-off="handingOff"
      @handoff="handoff"
    />
    <ChatInput
      :busy="busy"
      :can-stop="runActive || stopping"
      :stopping="stopping"
      :initial-draft="initialDraft"
      @send="send"
      @stop="stop"
    />
  </section>
</template>
