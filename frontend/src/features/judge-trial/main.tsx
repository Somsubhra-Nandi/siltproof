// Standalone entry for trial.html, so the feature runs without touching the
// main app's shell. The main app can instead mount <JudgeTrial /> itself.
import '../../maplibre-worker.ts'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '../../index.css'
import JudgeTrial from './JudgeTrial'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <JudgeTrial investigationHref="/" />
  </StrictMode>,
)
