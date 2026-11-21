<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { canReview } from '../../composables/useReviewActions'
import { useChanges } from '../../stores/changes'
import AppButton from '../common/AppButton.vue'
import AppModal from '../common/AppModal.vue'
import AppSpinner from '../common/AppSpinner.vue'
import ChangeStatusBadge from './ChangeStatusBadge.vue'

const props = defineProps<{ reviewBusy: boolean; approvingAll: boolean }>()
const emit = defineEmits<{ retry: []; approveAll: [] }>()
const store = useChanges()
const heading = useId()
const confirming = ref(false)
const hasReviewable = computed(() => store.changeSets.some((set) => set.changes.some(canReview)))
const hasOtherConflict = computed(() =>
  store.changeSets.some((set) =>
    set.changes.some(
      (change) => change.status === 'conflict' && change.id !== store.selectedChangeId,
    ),
  ),
)

function confirmApproveAll() {
  if (props.reviewBusy || !hasReviewable.value) return
  confirming.value = false
  emit('approveAll')
}
</script>

<template>
  <aside
    :aria-labelledby="heading"
    class="min-w-0 space-y-3 rounded-md border border-slate-200 p-3 dark:border-slate-800"
  >
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 :id="heading" class="text-lg font-semibold">Changes</h2>
      <AppButton
        variant="secondary"
        :disabled="reviewBusy || !hasReviewable"
        :loading="approvingAll"
        @click="confirming = true"
      >
        Approve all pending
      </AppButton>
    </div>
    <p v-if="hasOtherConflict" role="status" class="text-sm text-amber-900 dark:text-amber-200">
      Patches marked Conflict could not be applied.
    </p>
    <AppSpinner v-if="store.loading && !store.changeSets.length" label="Loading changes" />
    <div v-if="store.error" class="space-y-2">
      <p role="alert" class="text-sm text-red-700 dark:text-red-300">{{ store.error }}</p>
      <AppButton variant="secondary" @click="$emit('retry')">Retry changes</AppButton>
    </div>
    <p
      v-else-if="!store.loading && !store.changeSets.length"
      class="text-sm text-slate-600 dark:text-slate-400"
    >
      No changes proposed yet.
    </p>
    <ol v-if="store.changeSets.length" :aria-busy="store.loading" class="space-y-4">
      <li v-for="set in store.changeSets" :key="set.id" class="space-y-2">
        <h3 class="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span class="font-semibold">Change set #{{ set.id }}</span>
          <time :datetime="set.created_at" class="text-xs text-slate-600 dark:text-slate-400">
            {{ new Date(set.created_at).toLocaleString() }}
          </time>
        </h3>
        <ul class="space-y-1">
          <li v-for="change in set.changes" :key="change.id">
            <button
              :id="`change-${change.id}`"
              type="button"
              :aria-pressed="store.selectedChangeId === change.id"
              class="block w-full space-y-1 rounded-md border p-2 text-left text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:focus-visible:outline-blue-400"
              :class="
                store.selectedChangeId === change.id
                  ? 'border-blue-600 bg-blue-50 dark:border-blue-400 dark:bg-blue-950'
                  : 'border-slate-200 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800'
              "
              @click="store.selectedChangeId = change.id"
            >
              <span class="block break-all font-mono">{{ change.file_path }}</span>
              <span class="flex flex-wrap items-center gap-2">
                <ChangeStatusBadge :status="change.status" />
                <span class="text-xs text-slate-600 dark:text-slate-400">
                  {{ change.hunks.length }} {{ change.hunks.length === 1 ? 'hunk' : 'hunks' }}
                </span>
              </span>
            </button>
          </li>
        </ul>
      </li>
    </ol>
    <AppModal
      :open="confirming"
      title="Approve all pending changes?"
      description="This applies all remaining reviewable hunks without overriding prior decisions."
      @close="confirming = false"
    >
      <template #footer>
        <div class="flex flex-wrap justify-end gap-2">
          <AppButton variant="secondary" autofocus @click="confirming = false">Cancel</AppButton>
          <AppButton :disabled="reviewBusy || !hasReviewable" @click="confirmApproveAll">
            Approve all pending
          </AppButton>
        </div>
      </template>
    </AppModal>
  </aside>
</template>
