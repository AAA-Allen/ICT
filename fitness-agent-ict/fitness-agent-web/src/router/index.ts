import { createRouter, createWebHashHistory, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

const online = import.meta.env.MODE === 'online'

const router = createRouter({
  history: online
    ? createWebHashHistory(import.meta.env.BASE_URL)
    : createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    {
      path: '/live/:step(training|summary)?',
      name: 'live',
      component: () => import('../views/LiveSquatView.vue'),
    },
    ...(online ? [{ path: '/:pathMatch(.*)*', redirect: { name: 'home' } }] : []),
  ],
})

export default router
