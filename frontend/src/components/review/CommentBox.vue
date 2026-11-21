<script setup lang="ts">
import { ref, useId } from 'vue'
import type { components } from '../../api/schema'
import AppButton from '../common/AppButton.vue'
import AppField from '../common/AppField.vue'

type Change = components['schemas']['ChangeRead']
type Comment = components['schemas']['CommentCreate']

const props = defineProps<{
  change: Change
  disabled: boolean
  submitComment: (changeId: Change['id'], comment: Comment) => Promise<boolean>
}>()
const text = ref('')
const hunkIndex = ref<number | null>(null)
const heading = useId()
const scope = useId()

async function submit() {
  const comment = text.value.trim()
  if (props.disabled || !comment) return
  const index = hunkIndex.value
  if (index !== null && !props.change.hunks.some((hunk) => hunk.index === index)) return
  const accepted = await props.submitComment(
    props.change.id,
    index === null ? { text: comment } : { text: comment, hunk_index: index },
  )
  if (accepted) text.value = ''
}
</script>

<template>
  <section :aria-labelledby="heading" class="space-y-3">
    <h2 :id="heading" class="font-semibold">Review comments</h2>
    <ul v-if="change.comments.length" class="space-y-2 text-sm">
      <li
        v-for="comment in change.comments"
        :key="comment.id"
        class="rounded-md bg-slate-100 p-2 dark:bg-slate-800"
      >
        <p class="font-medium">
          {{ comment.hunk_index === null ? 'Whole change' : `Hunk ${comment.hunk_index + 1}` }}
        </p>
        <p class="whitespace-pre-wrap break-words">{{ comment.text }}</p>
      </li>
    </ul>
    <form class="space-y-2" @submit.prevent="submit">
      <div v-if="change.hunks.length" class="space-y-1">
        <label :for="scope" class="block text-sm font-medium">Comment on</label>
        <select
          :id="scope"
          v-model="hunkIndex"
          :disabled="disabled"
          class="block max-w-full rounded-md border border-slate-400 bg-white px-2 py-1 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 disabled:opacity-60 dark:border-slate-600 dark:bg-slate-900 dark:focus-visible:outline-blue-400"
        >
          <option :value="null">Whole change</option>
          <option v-for="hunk in change.hunks" :key="hunk.id" :value="hunk.index">
            Hunk {{ hunk.index + 1 }}: {{ hunk.header }}
          </option>
        </select>
      </div>
      <AppField v-slot="{ controlAttrs }" label="Revision comment">
        <textarea v-bind="controlAttrs" v-model="text" :disabled="disabled" rows="3" required />
      </AppField>
      <AppButton type="submit" :disabled="disabled || !text.trim()">Request revision</AppButton>
    </form>
  </section>
</template>
