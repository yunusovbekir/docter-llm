import { onScopeDispose, ref, watch, type Ref } from 'vue'
import { api, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useChanges } from '../stores/changes'
import { useSession } from '../stores/session'
import { useToast } from '../stores/toast'

type SessionId = components['schemas']['SessionDetail']['id']
type Change = components['schemas']['ChangeRead']
type ReviewAction = 'approve' | 'deny' | 'approve-all'

const reviewable = new Set<Change['status']>(['pending', 'approved', 'partial', 'conflict'])

export function canComment(change: Change) {
  return change.status === 'revised' || reviewable.has(change.status)
}

export function canReview(change: Change) {
  return (
    reviewable.has(change.status) &&
    (!change.hunks.length || change.hunks.some(({ status }) => reviewable.has(status)))
  )
}

export function useReviewActions(sessionId: Readonly<Ref<SessionId | undefined>>) {
  const changes = useChanges()
  const session = useSession()
  const toast = useToast()
  const pending = ref<ReviewAction>()
  let active: AbortController | undefined

  function reset() {
    active?.abort()
    active = undefined
    pending.value = undefined
  }
  watch(sessionId, reset)
  onScopeDispose(reset)

  async function review(action: ReviewAction, hunks?: number[]) {
    const id = sessionId.value
    const change = changes.selectedChange
    if (pending.value || changes.loading || id === undefined || session.session?.id !== id) return
    if (action !== 'approve-all' && (!change || !canReview(change))) return
    if (
      hunks &&
      (!hunks.length ||
        hunks.some(
          (index) =>
            !change?.hunks.some((hunk) => hunk.index === index && hunk.status === 'pending'),
        ))
    )
      return
    if (action === 'approve-all' && !changes.changeSets.some((set) => set.changes.some(canReview)))
      return

    const request = new AbortController()
    active = request
    pending.value = action
    const { signal } = request
    try {
      if (action === 'approve-all') {
        await unwrap(
          api.POST('/sessions/{session_id}/changes/approve-all', {
            params: { path: { session_id: id } },
            signal,
          }),
        )
      } else if (change) {
        const path =
          action === 'approve' ? '/changes/{change_id}/approve' : '/changes/{change_id}/deny'
        await unwrap(
          api.POST(path, {
            params: { path: { change_id: change.id } },
            ...(hunks && { body: { hunks } }),
            signal,
          }),
        )
      }
    } catch (cause) {
      if (!signal.aborted) {
        toast.error(cause instanceof ApiError ? cause.message : 'Could not review the changes.')
      }
    } finally {
      // A bulk action can persist some changes before a later operation fails.
      if (!signal.aborted && sessionId.value === id) {
        await Promise.all([changes.load(id), session.refresh(id)])
      }
      if (active === request) reset()
    }
  }

  return { pending, review }
}
