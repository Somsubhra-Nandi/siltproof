import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'

// jsdom has no WebGL, so the real map cannot start. The dashboard's logic -
// loading, selecting, deciding - is what these tests are about, so the map is
// replaced by a marker element.
vi.mock('../components/MapView', () => ({
  default: () => null,
}))
