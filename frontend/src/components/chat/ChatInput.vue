<script setup lang="ts">
import { ref } from 'vue'
import AppButton from '../common/AppButton.vue'
import AppField from '../common/AppField.vue'

const props = defineProps<{ busy: boolean; canStop: boolean; stopping: boolean }>()
const emit = defineEmits<{ send: [text: string]; stop: [] }>()
const draft = ref('')

function send() {
  const text = draft.value.trim()
  if (props.busy || !text) return
  emit('send', text)
  draft.value = ''
}

function onEnter(event: globalThis.KeyboardEvent) {
  if (event.shiftKey || event.isComposing) return
  event.preventDefault()
  send()
}
</script>

<template>
  <form class="space-y-2" @submit.prevent="send">
    <AppField
      v-slot="{ controlAttrs }"
      label="Message"
      hint="Enter to send. Shift+Enter for a new line."
    >
      <textarea
        v-bind="controlAttrs"
        v-model="draft"
        :disabled="busy"
        rows="3"
        @keydown.enter="onEnter"
      />
    </AppField>
    <div class="flex gap-2">
      <AppButton type="submit" :disabled="busy || !draft.trim()">Send</AppButton>
      <AppButton v-if="canStop" variant="secondary" :loading="stopping" @click="emit('stop')">
        Stop
      </AppButton>
    </div>
  </form>
</template>
