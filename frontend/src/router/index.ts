import { createRouter, type RouterHistory } from 'vue-router'
import HistoryView from '../views/HistoryView.vue'
import NotFoundView from '../views/NotFoundView.vue'
import ProjectDetailView from '../views/ProjectDetailView.vue'
import ProjectsView from '../views/ProjectsView.vue'
import SessionView from '../views/SessionView.vue'
import SettingsView from '../views/SettingsView.vue'

export function createAppRouter(history: RouterHistory) {
  return createRouter({
    history,
    routes: [
      { path: '/', redirect: '/projects' },
      {
        path: '/projects',
        children: [
          { path: '', component: ProjectsView },
          { path: ':id', component: ProjectDetailView, props: true },
        ],
      },
      { path: '/sessions/:id', component: SessionView },
      { path: '/history', component: HistoryView },
      { path: '/settings', component: SettingsView },
      { path: '/:pathMatch(.*)*', component: NotFoundView },
    ],
  })
}
