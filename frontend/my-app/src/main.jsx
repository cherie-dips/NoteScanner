import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './embed.css'
import App from './App.jsx'
import { APP_NAME, EMBEDDED } from './embed'

if (EMBEDDED) {
  // embed.css switches the palette to SDE-Prep's under this attribute.
  document.documentElement.dataset.embed = 'sde'
  document.title = APP_NAME
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Installable app + offline shell (production builds only, so development always loads fresh code).
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register(`${import.meta.env.BASE_URL}sw.js`, { scope: import.meta.env.BASE_URL })
      .catch(() => {})
  })
}
