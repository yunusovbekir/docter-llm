<script setup lang="ts">
import type { components } from '../../api/schema'
import AppButton from '../common/AppButton.vue'

defineProps<{
  status: components['schemas']['ChangeStatus']
  disabled: boolean
  pending: 'approve' | 'deny' | 'approve-all' | undefined
}>()
defineEmits<{ approve: []; deny: [] }>()
</script>

<template>
  <section aria-label="Review selected change" class="space-y-2">
    <p
      v-if="status === 'conflict'"
      role="alert"
      class="rounded-md bg-amber-50 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200"
    >
      The patch could not be applied because it conflicts with the current file.
    </p>
    <div class="flex flex-wrap gap-2">
      <AppButton :disabled="disabled" :loading="pending === 'approve'" @click="$emit('approve')">
        Approve
      </AppButton>
      <AppButton
        variant="danger"
        :disabled="disabled"
        :loading="pending === 'deny'"
        @click="$emit('deny')"
      >
        Deny
      </AppButton>
    </div>
  </section>
</template>
