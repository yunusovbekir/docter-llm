import { onScopeDispose, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import type { components } from '../api/schema'
import { createSession } from '../api/sessions'
import { useSession } from '../stores/session'
import { useToast } from '../stores/toast'
import type { CoverageData } from './coverageReport'

export function useCoverageHandoff(
  projectId: components['schemas']['SessionCreate']['project_id'],
) {
  const router = useRouter()
  const toast = useToast()
  const store = useSession()
  const handingOff = ref(false)
  const controller = new AbortController()
  const { signal } = controller
  onScopeDispose(() => controller.abort())

  async function handoff(rows: CoverageData['rows']) {
    if (handingOff.value || !rows.length) return
    handingOff.value = true
    try {
      const session = await createSession({ project_id: projectId, mode: 'add' }, signal)
      if (signal.aborted) return
      const symbols = rows.map(({ qualname, file, line }) => `- ${qualname} (${file}:${line})`)
      store.handoffDraft = {
        id: session.id,
        text: ['Add missing documentation for these symbols:', ...symbols].join('\n'),
      }
      try {
        const failure = await router.push(`/sessions/${session.id}`)
        if (failure) throw failure
      } catch (error) {
        if (store.handoffDraft?.id === session.id) store.handoffDraft = undefined
        throw error
      }
    } catch (error) {
      if (!signal.aborted) {
        toast.error(error instanceof ApiError ? error.message : 'Could not open the Add session.')
      }
    } finally {
      handingOff.value = false
    }
  }

  return { handingOff, handoff }
}
