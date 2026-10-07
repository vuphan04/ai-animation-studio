import React from 'react'
import { createRoot } from 'react-dom/client'
import { loadLanguage, t } from './i18n'
import './style.css'

try {
  document.documentElement.dataset.theme = localStorage.getItem('studio_theme') === 'day' ? 'day' : 'night'
} catch {
  document.documentElement.dataset.theme = 'night'
}

// The interface text is in place before any screen module is evaluated.
loadLanguage().then(() => import('./App')).then(({ default: App }) => {
  document.title = t('AI Animation Studio · Không gian sản xuất')
  createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
})
