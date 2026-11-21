<script setup lang="ts">
import type { components } from '../../api/schema'
import AppBadge from '../common/AppBadge.vue'

defineProps<{ status: components['schemas']['ChangeStatus'] }>()
const statuses = {
  pending: { label: 'Pending', tone: 'warning' },
  approved: { label: 'Approved', tone: 'success' },
  applied: { label: 'Applied', tone: 'success' },
  denied: { label: 'Denied', tone: 'neutral' },
  partial: { label: 'Partial', tone: 'warning' },
  conflict: { label: 'Conflict', tone: 'danger' },
  revised: { label: 'Revised', tone: 'info' },
} as const satisfies Record<components['schemas']['ChangeStatus'], { label: string; tone: string }>
</script>

<template>
  <AppBadge :tone="statuses[status].tone">{{ statuses[status].label }}</AppBadge>
</template>
