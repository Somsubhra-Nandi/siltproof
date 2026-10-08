import { setWorkerUrl } from 'maplibre-gl'
// `?url` makes Vite emit the worker as a real asset and hand back its hashed
// path. Without this the bundle still asks for maplibre-gl-worker.mjs, but
// the build never writes one, so the worker 404s and the map renders nothing
// at all - a blank panel with "Worker failed to load".
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'

// Same-origin and bundled with the app, so the offline mode stays offline.
setWorkerUrl(workerUrl)
