import { defineStore } from 'pinia'
import { onScopeDispose, ref } from 'vue'
import { api, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useToast } from './toast'

type ChangeSet = components['schemas']['ChangeSetRead']
type ChangeId = components['schemas']['ChangeRead']['id']

export const useChanges = defineStore('changes', () => {
  const changeSets = ref<ChangeSet[]>([])
  const selectedChangeId = ref<ChangeId | null>(null)
  const loading = ref(false)
  const error = ref('')
  const toast = useToast()
  let currentSession: ChangeSet['session_id'] | undefined
  let active: AbortController | undefined

  function clear() {
    active?.abort()
    active = undefined
    currentSession = undefined
    changeSets.value = []
    selectedChangeId.value = null
    loading.value = false
    error.value = ''
  }

  onScopeDispose(clear)

  async function load(sessionId: ChangeSet['session_id']) {
    if (currentSession !== sessionId) clear()
    currentSession = sessionId
    active?.abort()
    const request = new AbortController()
    active = request
    loading.value = true
    error.value = ''
    try {
      const result = await unwrap(
        api.GET('/sessions/{session_id}/changes', {
          params: { path: { session_id: sessionId } },
          signal: request.signal,
        }),
      )
      if (request.signal.aborted) return
      if (!result) throw new ApiError('invalid_response', 'The API returned no change sets.', 0)
      changeSets.value = result
        .filter((set) => set.changes.length)
        .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id - a.id)
      if (
        !changeSets.value.some((set) => set.changes.some(({ id }) => id === selectedChangeId.value))
      ) {
        selectedChangeId.value = null
      }
    } catch (cause) {
      if (request.signal.aborted) return
      error.value = cause instanceof ApiError ? cause.message : 'Could not load the changes.'
      toast.error(error.value)
    } finally {
      if (active === request) {
        active = undefined
        loading.value = false
      }
    }
  }

  return { changeSets, selectedChangeId, loading, error, load, clear }
})
