import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, expect, it } from 'vitest'
import AppBadge from './AppBadge.vue'
import AppButton from './AppButton.vue'
import AppCard from './AppCard.vue'
import AppEmptyState from './AppEmptyState.vue'
import AppInput from './AppInput.vue'
import AppSelect from './AppSelect.vue'
import AppSpinner from './AppSpinner.vue'

enableAutoUnmount(afterEach)

it('renders a safe button type by default and emits clicks only while enabled and idle', async () => {
  const wrapper = mount(AppButton, { slots: { default: 'Save' } })
  const button = wrapper.get('button')
  expect(button.attributes('type')).toBe('button')
  await button.trigger('click')
  expect(wrapper.emitted('click')).toEqual([[expect.any(MouseEvent)]])

  await wrapper.setProps({ loading: true, type: 'submit' })
  expect(button.element.disabled).toBe(true)
  expect(button.attributes('aria-busy')).toBe('true')
  expect(button.attributes('type')).toBe('submit')
  expect(button.text()).toBe('Save')
  expect(wrapper.getComponent(AppSpinner).props('decorative')).toBe(true)
  await button.trigger('click')

  await wrapper.setProps({ loading: false, disabled: true })
  expect(button.element.disabled).toBe(true)
  expect(button.attributes('aria-busy')).toBeUndefined()
  expect(wrapper.findComponent(AppSpinner).exists()).toBe(false)
  await button.trigger('click')
  expect(wrapper.emitted('click')).toHaveLength(1)
})

it.each([
  ['primary', 'bg-blue-700'],
  ['secondary', 'bg-slate-200'],
  ['danger', 'bg-red-700'],
  ['ghost', 'text-slate-700'],
] as const)('styles the %s button variant', (variant, style) => {
  expect(mount(AppButton, { props: { variant } }).classes()).toContain(style)
})

it('labels and describes the input, forwards native attributes, and emits value updates', async () => {
  const wrapper = mount(AppInput, {
    props: {
      label: 'Name',
      modelValue: 'Docter',
      hint: 'Use a short name',
      error: 'Already taken',
    },
    attrs: { name: 'project', required: true },
  })
  const input = wrapper.get('input')
  const { id } = input.element
  expect(wrapper.get('label').element.htmlFor).toBe(id)
  expect(input.attributes('aria-describedby')).toBe(`${id}-error ${id}-hint`)
  expect(wrapper.get(`[id="${id}-error"]`).text()).toBe('Already taken')
  expect(wrapper.get(`[id="${id}-hint"]`).text()).toBe('Use a short name')
  expect(input.attributes('aria-invalid')).toBe('true')
  expect(input.element.name).toBe('project')
  expect(input.element.required).toBe(true)
  expect(input.element.value).toBe('Docter')
  await input.setValue('Another project')
  expect(wrapper.emitted('update:modelValue')).toEqual([['Another project']])

  await wrapper.setProps({ error: undefined })
  expect(input.attributes('aria-invalid')).toBeUndefined()
  expect(input.attributes('aria-describedby')).toBe(`${id}-hint`)
})

it('reveals and hides a password without submitting the form or changing the value', async () => {
  const wrapper = mount(AppInput, {
    props: { type: 'password', label: 'Password', modelValue: 'secret', id: 'password' },
  })
  const input = wrapper.get('input')
  const toggle = wrapper.get('button')
  expect(input.element.type).toBe('password')
  expect(toggle.element.type).toBe('button')
  expect(toggle.attributes('aria-controls')).toBe('password')
  expect(toggle.attributes('aria-label')).toBe('Show Password')
  await toggle.trigger('click')
  expect(input.element.type).toBe('text')
  expect(toggle.attributes('aria-label')).toBe('Hide Password')
  await toggle.trigger('click')
  expect(input.element.type).toBe('password')
  expect(input.element.value).toBe('secret')
  expect(wrapper.emitted('update:modelValue')).toBeUndefined()

  await wrapper.setProps({ disabled: true })
  expect(input.element.disabled).toBe(true)
  expect(toggle.element.disabled).toBe(true)
})

it('labels a native select, respects disabled options, and emits the chosen value', async () => {
  const wrapper = mount(AppSelect, {
    props: {
      id: 'language',
      label: 'Language',
      hint: 'Choose a language',
      modelValue: 'en',
      options: [
        { value: 'en', label: 'English' },
        { value: 'fr', label: 'French' },
        { value: 'de', label: 'German', disabled: true },
      ],
    },
    attrs: { name: 'language' },
  })
  const select = wrapper.get('select')
  expect(wrapper.get('label[for="language"]').text()).toBe('Language')
  expect(select.attributes('aria-describedby')).toBe('language-hint')
  expect(select.element.name).toBe('language')
  expect(select.element.value).toBe('en')
  expect(wrapper.get('option[value="de"]').attributes('disabled')).toBeDefined()
  await select.setValue('fr')
  expect(wrapper.emitted('update:modelValue')).toEqual([['fr']])

  await wrapper.setProps({ disabled: true, error: 'Unavailable' })
  expect(select.element.disabled).toBe(true)
  expect(select.attributes('aria-invalid')).toBe('true')
  expect(select.attributes('aria-describedby')).toBe('language-error language-hint')
})

it.each([
  ['neutral', 'bg-slate-100'],
  ['info', 'bg-blue-100'],
  ['success', 'bg-emerald-100'],
  ['warning', 'bg-amber-100'],
  ['danger', 'bg-red-100'],
] as const)('styles the %s badge tone and keeps its text visible', (tone, style) => {
  const wrapper = mount(AppBadge, { props: { tone }, slots: { default: 'Status' } })
  expect(wrapper.classes()).toContain(style)
  expect(wrapper.text()).toBe('Status')
})

it('renders the card as the requested element with named and default slots', () => {
  const wrapper = mount(AppCard, {
    props: { as: 'article' },
    slots: { header: '<h2>Title</h2>', default: '<p>Content</p>', footer: 'Details' },
  })
  expect(wrapper.element.tagName).toBe('ARTICLE')
  expect(wrapper.get('header h2').text()).toBe('Title')
  expect(wrapper.get('p').text()).toBe('Content')
  expect(wrapper.get('footer').text()).toBe('Details')
})

it('announces the spinner label only when the spinner is not decorative', async () => {
  const wrapper = mount(AppSpinner, { props: { label: 'Saving' } })
  expect(wrapper.get('[role="status"]').text()).toBe('Saving')
  await wrapper.setProps({ decorative: true })
  expect(wrapper.attributes('aria-hidden')).toBe('true')
  expect(wrapper.attributes('role')).toBeUndefined()
  expect(wrapper.text()).toBe('')
})

it('renders an empty-state heading, optional description, and actions', async () => {
  const wrapper = mount(AppEmptyState, {
    props: { title: 'No projects' },
    slots: { actions: '<button>Create a project</button>' },
  })
  expect(wrapper.get('h2').text()).toBe('No projects')
  expect(wrapper.find('p').exists()).toBe(false)
  expect(wrapper.get('button').text()).toBe('Create a project')
  await wrapper.setProps({ description: 'Add a repository to get started.' })
  expect(wrapper.get('p').text()).toBe('Add a repository to get started.')
})
