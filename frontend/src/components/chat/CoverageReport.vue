<script setup lang="ts">
import { computed, ref } from 'vue'
import type { CoverageData } from '../../composables/coverageReport'
import AppButton from '../common/AppButton.vue'
import AppField from '../common/AppField.vue'

const props = defineProps<{ report: CoverageData; handingOff: boolean }>()
const emit = defineEmits<{ handoff: [rows: CoverageData['rows']] }>()
const missingType = ref('')
const ascending = ref(true)
const selected = ref<number[]>([])
const indexedRows = computed(() => props.report.rows.map((row, index) => ({ row, index })))
const tags = computed(() => [...new Set(props.report.rows.flatMap((row) => row.missing))].sort())
const visibleRows = computed(() =>
  indexedRows.value
    .filter(({ row }) => !missingType.value || row.missing.includes(missingType.value))
    .sort(
      (a, b) =>
        (ascending.value ? 1 : -1) * a.row.file.localeCompare(b.row.file) ||
        a.row.line - b.row.line,
    ),
)

function handoff() {
  const indices = new Set(selected.value)
  const rows = indexedRows.value.filter(({ index }) => indices.has(index)).map(({ row }) => row)
  if (rows.length) emit('handoff', rows)
}
</script>

<template>
  <section
    aria-label="Documentation coverage report"
    class="space-y-3 rounded-md border border-slate-200 bg-white p-3 text-sm dark:border-slate-700 dark:bg-slate-900"
  >
    <p class="font-semibold">Documentation coverage: {{ report.percent }}%</p>
    <progress
      aria-label="Documentation coverage"
      class="w-full accent-blue-700 dark:accent-blue-400"
      :value="report.percent"
      max="100"
    />
    <template v-if="report.rows.length">
      <AppField v-slot="{ controlAttrs }" label="Missing type">
        <select v-bind="controlAttrs" v-model="missingType">
          <option value="">All missing types</option>
          <option v-for="tag in tags" :key="tag" :value="tag">{{ tag }}</option>
        </select>
      </AppField>
      <div class="overflow-x-auto">
        <table aria-label="Symbols missing documentation" class="w-full text-left">
          <thead>
            <tr class="border-b border-slate-200 dark:border-slate-700">
              <th scope="col" class="p-2">Select</th>
              <th scope="col" class="p-2">Qualname</th>
              <th scope="col" class="p-2">Kind</th>
              <th scope="col" class="p-2" :aria-sort="ascending ? 'ascending' : 'descending'">
                <button
                  type="button"
                  class="whitespace-nowrap rounded underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 dark:focus-visible:outline-blue-400"
                  @click="ascending = !ascending"
                >
                  File:line <span aria-hidden="true">{{ ascending ? '↑' : '↓' }}</span>
                </button>
              </th>
              <th scope="col" class="p-2">Missing tags</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="{ row, index } in visibleRows"
              :key="index"
              class="border-b border-slate-200 align-top dark:border-slate-700"
            >
              <td class="p-2">
                <input
                  v-model="selected"
                  type="checkbox"
                  :value="index"
                  :aria-label="`Select ${row.qualname} (${row.file}:${row.line})`"
                  class="accent-blue-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 dark:accent-blue-400 dark:focus-visible:outline-blue-400"
                />
              </td>
              <td class="p-2 font-mono">{{ row.qualname }}</td>
              <td class="p-2">{{ row.kind }}</td>
              <td class="p-2 font-mono">{{ row.file }}:{{ row.line }}</td>
              <td class="p-2">{{ row.missing.join(', ') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!visibleRows.length">No symbols match this filter.</p>
      <div class="flex flex-wrap items-center gap-2">
        <AppButton :disabled="!selected.length" :loading="handingOff" @click="handoff">
          Send to Add mode
        </AppButton>
        <span>{{ selected.length }} selected</span>
      </div>
    </template>
    <p v-else>No documentation gaps found.</p>
  </section>
</template>
