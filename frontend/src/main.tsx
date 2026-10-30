import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/inter/wght.css'
import '@fontsource/archivo/latin-700.css'
import '@fontsource/archivo/latin-800.css'
import '@fontsource/archivo/latin-ext-700.css'
import '@fontsource/archivo/latin-ext-800.css'
import { App } from './App'
import { applyTheme, readTheme } from './theme/theme'
import './styles/tokens.css'
import './styles/tailwind.css'
import './styles/app.css'
import './styles/forms.css'
import './styles/classes.css'

// theme-init.js sets the theme before first paint; this keeps it if that script was blocked.
applyTheme(readTheme())

const root = document.getElementById('root')
if (!root) throw new Error('Missing #root element in index.html')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
