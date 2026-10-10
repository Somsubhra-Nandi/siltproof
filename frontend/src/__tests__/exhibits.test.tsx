import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import ExhibitPhotos from '../components/ExhibitPhotos'
import ExhibitSlip from '../components/ExhibitSlip'
import { caseFacts } from '../lib/caseFacts'
import type { Drain, Photo } from '../types'
import d1 from '../../public/data/demo/drain-1.json'
import d9 from '../../public/data/demo/drain-9.json'
import d11 from '../../public/data/demo/drain-11.json'
import d14 from '../../public/data/demo/drain-14.json'

const drain = (json: unknown) => json as Drain
const noop = () => undefined

function photos(d: Drain, original: Photo | null = null) {
  return render(
    <ExhibitPhotos
      drain={d}
      facts={caseFacts(d)}
      original={original}
      forceSlot={false}
      expired={false}
      simulatedCase
      onImageError={noop}
    />,
  )
}

describe('Exhibit C, from the snapshot', () => {
  it('shows a clean drain’s before and after photos with captions, not placeholders', () => {
    const { container } = photos(drain(d1))
    const imgs = container.querySelectorAll('.photo-open img')
    expect([...imgs].map((img) => img.getAttribute('src'))).toEqual([
      '/data/demo/evidence/photos/B1/drain1/before-01.jpg',
      '/data/demo/evidence/photos/B1/drain1/after-01.jpg',
    ])
    expect(container.querySelector('.photoslot')).toBeNull()
    expect(screen.queryByText(/Simulated photo/)).toBeNull()
    expect(screen.getByText(/Generated stand-in images/)).toBeInTheDocument()
  })

  it('enlarges a photo in a dialog, steps through the drain’s photos, and closes on Escape', () => {
    photos(drain(d9))
    fireEvent.click(screen.getByRole('button', { name: /Enlarge the before photo/ }))
    const dialog = screen.getByRole('dialog', { name: /Drain 9 photographs/ })
    expect(within(dialog).getByText(/1 of 3/)).toBeInTheDocument()
    expect(within(dialog).getByRole('img')).toHaveAttribute('src', expect.stringContaining('drain9/before-01.jpg'))
    fireEvent.keyDown(dialog, { key: 'ArrowRight' })
    expect(within(dialog).getByRole('img')).toHaveAttribute('src', expect.stringContaining('drain9/after-01.jpg'))
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('labels a pre-written observation honestly', () => {
    const { container } = photos(drain(d1))
    const ai = container.querySelector('.ai') as HTMLElement
    expect(within(ai).getByText('Pre-written observation, not a finding')).toBeInTheDocument()
    expect(within(ai).queryByText(/Bedrock/)).toBeNull()
  })

  it('keeps drain 14’s reuse comparison and enlarges both photos', () => {
    const original = drain(d9).photos.find((p) => p.s3Key === 'photos/B1/drain9/after-01.jpg')!
    const { container } = photos(drain(d14), original)
    expect(container.querySelector('.compare')).not.toBeNull()
    expect(screen.getByText(/0 of 64/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Enlarge both photos' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/1 of 2/)).toBeInTheDocument()
  })
})

describe('Exhibit B, from the snapshot', () => {
  it('shows drain 11’s slip, highlights the net row and enlarges it', () => {
    const d = drain(d11)
    const facts = caseFacts(d)
    const { container } = render(
      <ExhibitSlip trip={facts.trip} facts={facts} imageExpired={false} simulatedCase onImageError={noop} />,
    )
    expect(container.querySelector('.scan img')).toHaveAttribute('src', '/data/demo/evidence/slips/B1/11-001.png')
    expect(container.querySelector('.scan .hl')).not.toBeNull()
    expect(container.querySelector('.fields .hit')?.textContent).toContain('14.00 t')
    expect(screen.queryByText('Sample slip')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Enlarge the weighbridge slip for trip 001/ }))
    const dialog = screen.getByRole('dialog', { name: /Weighbridge slip, trip 001/ })
    expect(dialog.querySelector('.hl')).not.toBeNull()
    expect(within(dialog).getByText(/14.00 t on a truck rated 10 t/)).toBeInTheDocument()
  })

  it('follows the selected trip', () => {
    const d = drain(d11)
    const facts = caseFacts(d, '11#003')
    const { container } = render(
      <ExhibitSlip trip={facts.trip} facts={facts} imageExpired={false} simulatedCase onImageError={noop} />,
    )
    expect(container.querySelector('.scan img')).toHaveAttribute('src', '/data/demo/evidence/slips/B1/11-003.png')
    expect(container.querySelector('.scan .hl')).toBeNull()
  })
})
