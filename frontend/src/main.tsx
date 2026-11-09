import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/inter/wght.css'
import '@fontsource-variable/archivo/wdth.css'
import { App } from './App'
import { I18nProvider } from './i18n/I18n'
import { applyTheme, readTheme } from './theme/theme'
import './styles/tokens.css'
import './styles/tailwind.css'
import './styles/app.css'
import './styles/imagery.css'
import './styles/forms.css'
import './styles/captcha.css'
import './styles/classes.css'

// theme-init.js sets the theme before first paint; this keeps it if that script was blocked.
applyTheme(readTheme())

const root = document.getElementById('root')
if (!root) throw new Error('Missing #root element in index.html')

createRoot(root).render(
  <StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </StrictMode>,
)
