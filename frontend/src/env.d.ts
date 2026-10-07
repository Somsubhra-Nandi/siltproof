/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AWS_REGION: string
  readonly VITE_LOCATION_API_KEY: string
  readonly VITE_MAP_CENTER_LAT: string
  readonly VITE_MAP_CENTER_LON: string
  readonly VITE_MAP_ZOOM: string
  readonly VITE_API_BASE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
