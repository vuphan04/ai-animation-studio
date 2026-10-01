import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './style.css'

try {
  document.documentElement.dataset.theme = localStorage.getItem('studio_theme') === 'day' ? 'day' : 'night'
} catch {
  document.documentElement.dataset.theme = 'night'
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
