import { createApp } from 'vue'
import RoleManagerApp from './RoleManagerApp.vue'
import './styles.css'
import { applyTheme } from './composables/useTheme'

document.body.classList.add('role-manager-body')
applyTheme()
createApp(RoleManagerApp).mount('#app')
