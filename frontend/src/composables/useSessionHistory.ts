import { computed, onScopeDispose, ref, watch } from 'vue'
import { api, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useProjects } from '../stores/projects'
import { useToast } from '../stores/toast'

type SessionSummary = components['schemas']['SessionSummary']

export function useSessionHistory() {
  const projects = useProjects()
  const toast = useToast()
  const projectFilter = ref('')
  const projectNames = computed(() => new Map(projects.projects.map(({ id, name }) => [id, name])))
  const projectOptions = computed(() => [
    { value: '', label: 'All projects' },
    ...projects.projects.map(({ id, name }) => ({ value: String(id), label: name })),
  ])
  const sessions = ref<SessionSummary[]>([])
  const loading = ref(true)
  const loadError = ref('')
  const sessionToDelete = ref<SessionSummary | null>(null)
  const deleting = ref(false)
  const deleteError = ref('')
  const lifetime = new AbortController()
  let listRequest: AbortController | undefined

  onScopeDispose(() => {
    lifetime.abort()
    listRequest?.abort()
  })

  function notifyError(error: unknown, fallback: string) {
    const message = error instanceof ApiError ? error.message : fallback
    toast.error(message)
    return message
  }

  async function load() {
    listRequest?.abort()
    const { signal } = (listRequest = new AbortController())
    loading.value = true
    loadError.value = ''
    try {
      const result = await unwrap(
        api.GET('/sessions', {
          params: { query: projectFilter.value ? { project_id: Number(projectFilter.value) } : {} },
          signal,
        }),
      )
      if (!result) throw new ApiError('invalid_response', 'The API returned no session list.', 0)
      if (!signal.aborted) sessions.value = result
    } catch (error) {
      if (!signal.aborted) loadError.value = notifyError(error, 'Could not load sessions.')
    } finally {
      if (!signal.aborted) loading.value = false
    }
  }

  function openDelete(session: SessionSummary) {
    deleteError.value = ''
    sessionToDelete.value = session
  }

  function closeDelete() {
    if (!deleting.value) sessionToDelete.value = null
  }

  async function confirmDelete() {
    if (deleting.value || !sessionToDelete.value) return
    const { id } = sessionToDelete.value
    const { signal } = lifetime
    deleting.value = true
    deleteError.value = ''
    try {
      await unwrap(
        api.DELETE('/sessions/{session_id}', { params: { path: { session_id: id } }, signal }),
      )
      if (signal.aborted) return
      sessions.value = sessions.value.filter((session) => session.id !== id)
      sessionToDelete.value = null
      toast.success('Session deleted.')
    } catch (error) {
      if (!signal.aborted) deleteError.value = notifyError(error, 'Could not delete the session.')
    } finally {
      deleting.value = false
    }
  }

  async function copyBranch(branch: string) {
    try {
      await globalThis.navigator.clipboard.writeText(branch)
      if (!lifetime.signal.aborted) toast.success('Branch name copied.')
    } catch {
      if (!lifetime.signal.aborted) {
        toast.error('Could not copy the branch name. Select the branch name to copy it manually.')
      }
    }
  }

  watch(projectFilter, load, { immediate: true })
  void projects.list()

  return {
    projects,
    projectFilter,
    projectNames,
    projectOptions,
    sessions,
    loading,
    loadError,
    load,
    sessionToDelete,
    deleting,
    deleteError,
    openDelete,
    closeDelete,
    confirmDelete,
    copyBranch,
  }
}
