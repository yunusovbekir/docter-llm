import { ref, watch, type Ref } from 'vue'
import { api, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useToast } from '../stores/toast'

type ProjectId = components['schemas']['ProjectRead']['id']
type IndexStatus = components['schemas']['ProjectIndexStatus']

const POLL_INTERVAL_MS = 1_000

export function useIndexStatus(projectId: Readonly<Ref<ProjectId | undefined>>) {
  const status = ref<IndexStatus>()
  const loading = ref(false)
  const starting = ref(false)
  const statusError = ref('')
  const toast = useToast()
  let actions: { retry: () => Promise<void>; start: () => Promise<void> } | undefined

  watch(
    projectId,
    (id, _, onCleanup) => {
      status.value = undefined
      statusError.value = ''
      loading.value = false
      starting.value = false
      if (id === undefined) return

      const controller = new AbortController()
      const { signal } = controller
      const options = { params: { path: { project_id: id } }, signal }
      let timer: ReturnType<typeof setTimeout> | undefined
      onCleanup(() => {
        controller.abort()
        clearTimeout(timer)
        actions = undefined
      })

      function update(result: IndexStatus | undefined) {
        if (!result) {
          throw new ApiError('invalid_response', 'The API returned no indexing status.', 0)
        }
        status.value = result
        if (result.status === 'running') timer = setTimeout(readStatus, POLL_INTERVAL_MS)
      }

      async function readStatus() {
        if (loading.value) return
        loading.value = true
        statusError.value = ''
        clearTimeout(timer)
        try {
          const result = await unwrap(api.GET('/projects/{project_id}/index/status', options))
          if (!signal.aborted) update(result)
        } catch (cause) {
          if (signal.aborted) return
          statusError.value =
            cause instanceof ApiError ? cause.message : 'Could not load indexing status.'
          toast.error(statusError.value)
        } finally {
          if (!signal.aborted) loading.value = false
        }
      }

      async function startIndex() {
        if (
          !status.value ||
          status.value.status === 'running' ||
          statusError.value ||
          loading.value ||
          starting.value
        )
          return

        starting.value = true
        try {
          const result = await unwrap(api.POST('/projects/{project_id}/index', options))
          if (!signal.aborted) update(result)
        } catch (cause) {
          if (signal.aborted) return
          if (cause instanceof ApiError && cause.status === 409) {
            toast.warning('Indexing already running')
            await readStatus()
          } else {
            toast.error(cause instanceof ApiError ? cause.message : 'Could not start indexing.')
          }
        } finally {
          if (!signal.aborted) starting.value = false
        }
      }
      actions = { retry: readStatus, start: startIndex }
      void readStatus()
    },
    { immediate: true },
  )

  return {
    status,
    loading,
    starting,
    statusError,
    retry: () => actions?.retry(),
    startIndex: () => actions?.start(),
  }
}
