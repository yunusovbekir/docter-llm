import { computed, reactive, ref, watch } from 'vue'
import type { components } from '../api/schema'
import { useSettings } from '../stores/settings'

type SettingsUpdate = components['schemas']['SettingsUpdate']

const builtInProviders = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'ollama', label: 'Ollama' },
]

export const providerOptions = [...builtInProviders, { value: 'other', label: 'Other' }]

function validBaseUrl(value: string) {
  if (value.length > 500) return false
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol)
  } catch {
    return false
  }
}

export function useSettingsForm() {
  const store = useSettings()
  const draft = reactive({
    provider: '',
    customProvider: '',
    model: '',
    base_url: '',
    embedding_model: '',
    temperature: '',
    max_agent_steps: '',
    api_key: '',
  })
  const removeKey = ref(false)
  const provider = computed(() =>
    draft.provider === 'other' ? draft.customProvider.trim().toLowerCase() : draft.provider,
  )
  const hasKey = computed(
    () => !!store.settings?.has_api_key && provider.value === store.settings.provider,
  )

  watch(
    provider,
    () => {
      draft.api_key = ''
      removeKey.value = false
    },
    { flush: 'sync' },
  )
  watch(
    () => store.settings,
    (settings) => {
      if (!settings) return
      const builtIn = builtInProviders.some(({ value }) => value === settings.provider)
      Object.assign(draft, {
        provider: builtIn ? settings.provider : 'other',
        customProvider: builtIn ? '' : settings.provider,
        model: settings.model,
        base_url: settings.base_url ?? '',
        embedding_model: settings.embedding_model,
        temperature: String(settings.temperature),
        max_agent_steps: String(settings.max_agent_steps),
        api_key: '',
      })
      removeKey.value = false
    },
    { immediate: true },
  )

  const values = computed(() => ({
    provider: provider.value,
    model: draft.model.trim(),
    base_url: draft.base_url.trim() || null,
    embedding_model: draft.embedding_model.trim(),
    temperature: draft.temperature.trim() ? Number(draft.temperature) : NaN,
    max_agent_steps: draft.max_agent_steps.trim() ? Number(draft.max_agent_steps) : NaN,
  }))

  const errors = computed(() => {
    const { provider, model, base_url, embedding_model, temperature, max_agent_steps } =
      values.value
    return {
      provider: /^[a-z0-9_-]{1,64}$/.test(provider)
        ? undefined
        : 'Enter a provider ID (1-64 letters, numbers, underscores, or hyphens).',
      model: model && model.length <= 200 ? undefined : 'Enter a model (1-200 characters).',
      base_url:
        !base_url || validBaseUrl(base_url)
          ? undefined
          : 'Enter an HTTP or HTTPS URL (up to 500 characters).',
      embedding_model:
        embedding_model && embedding_model.length <= 200
          ? undefined
          : 'Enter an embedding model (1-200 characters).',
      temperature: temperature >= 0 && temperature <= 2 ? undefined : 'Enter a number from 0 to 2.',
      max_agent_steps:
        Number.isInteger(max_agent_steps) && max_agent_steps >= 1 && max_agent_steps <= 200
          ? undefined
          : 'Enter a whole number from 1 to 200.',
    }
  })

  const payload = computed<SettingsUpdate>(() => {
    const saved = store.settings
    if (!saved) return {}
    const next = values.value
    const body: SettingsUpdate = {}
    if (next.provider !== saved.provider) body.provider = next.provider
    if (next.model !== saved.model) body.model = next.model
    if (next.base_url !== saved.base_url) body.base_url = next.base_url
    if (next.embedding_model !== saved.embedding_model) body.embedding_model = next.embedding_model
    if (next.temperature !== saved.temperature) body.temperature = next.temperature
    if (next.max_agent_steps !== saved.max_agent_steps) body.max_agent_steps = next.max_agent_steps
    if (removeKey.value && hasKey.value) body.api_key = ''
    else if (draft.api_key) body.api_key = draft.api_key
    return body
  })
  const canSave = computed(
    () =>
      !store.loading &&
      !store.saving &&
      Object.keys(payload.value).length > 0 &&
      Object.values(errors.value).every((error) => !error),
  )

  function toggleKeyRemoval() {
    if (!hasKey.value) return
    draft.api_key = ''
    removeKey.value = !removeKey.value
  }

  async function save() {
    if (canSave.value) await store.save(payload.value)
  }

  return { draft, errors, hasKey, removeKey, canSave, toggleKeyRemoval, save }
}
