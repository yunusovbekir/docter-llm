import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useToast } from './toast'

type SessionDetail = components['schemas']['SessionDetail']
type Project = components['schemas']['ProjectRead']

export const useSession = defineStore('session', () => {
  const session = ref<SessionDetail>()
  const project = ref<Project>()
  const loading = ref(false)
  const error = ref('')
  const toast = useToast()
  let active: AbortController | undefined

  function clear() {
    active?.abort()
    active = undefined
    session.value = undefined
    project.value = undefined
    loading.value = false
    error.value = ''
  }

  async function load(id: SessionDetail['id']) {
    clear()
    const request = new AbortController()
    active = request
    const { signal } = request
    loading.value = true

    try {
      const detail = await unwrap(
        api.GET('/sessions/{session_id}', { params: { path: { session_id: id } }, signal }),
      )
      if (!detail) throw new ApiError('invalid_response', 'The API returned no session.', 0)
      if (signal.aborted) return
      session.value = detail

      try {
        const result = await unwrap(
          api.GET('/projects/{project_id}', {
            params: { path: { project_id: detail.project_id } },
            signal,
          }),
        )
        if (!result) throw new ApiError('invalid_response', 'The API returned no project.', 0)
        if (signal.aborted) return
        project.value = result
      } catch (cause) {
        if (!signal.aborted) {
          toast.error(cause instanceof ApiError ? cause.message : 'Could not load the project.')
        }
      }
    } catch (cause) {
      if (signal.aborted) return
      const message = cause instanceof ApiError ? cause.message : 'Could not load the session.'
      if (!(cause instanceof ApiError && cause.status === 404)) error.value = message
      toast.error(message)
    } finally {
      if (active === request) {
        active = undefined
        loading.value = false
      }
    }
  }

  return { session, project, loading, error, load, clear }
})
