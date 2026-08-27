import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { applyTheme, readTheme } from './theme'
import './styles.css'

// Before first paint, so the login screen honours the theme too.
applyTheme(readTheme())

const el = document.getElementById('root')
if (!el) throw new Error('Missing #root')

createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
