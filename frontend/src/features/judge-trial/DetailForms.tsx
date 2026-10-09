// Forms for the details that are typed rather than uploaded: drain location,
// disposal site, claim, work window and truck. Each saves on its own, and
// shows the server's per-field errors, which are the ones that count.

import { useState } from 'react'
import type { ReactNode } from 'react'
import { claimArithmetic, formatLatLon, optionalNumber, parseLatLon } from './logic'
import type { Claim, DisposalSite, DrainLocation, LonLat, Truck, WorkWindow } from './types'

export type SaveResult = { ok: true } | { ok: false; fields: Record<string, string>; message: string }
export type Save = (body: Record<string, unknown>) => Promise<SaveResult>

function FieldError({ errors, name }: { errors: Record<string, string>; name: string }) {
  const message = errors[name]
  return message ? <span className="jt-field-error">{message}</span> : null
}

function SaveRow({ busy, saved, message, onClear, children }: {
  busy: boolean; saved: boolean; message: string | null; onClear?: () => void; children?: ReactNode
}) {
  return (
    <div className="jt-save-row">
      <button type="submit" className={`btn btn-quiet${busy ? ' is-busy' : ''}`} disabled={busy}>
        {busy && <span className="spinner" aria-hidden />}Save
      </button>
      {onClear && (
        <button type="button" className="link" onClick={onClear} disabled={busy}>Clear</button>
      )}
      {children}
      {saved && !message && <span className="jt-saved" role="status">Saved</span>}
      {message && <span className="jt-error" role="alert">{message}</span>}
    </div>
  )
}

function useSaver(save: Save, name: string) {
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [message, setMessage] = useState<string | null>(null)

  async function run(value: unknown, localErrors: Record<string, string> = {}) {
    setSaved(false)
    if (Object.keys(localErrors).length) {
      setErrors(localErrors)
      setMessage('Fix the highlighted fields.')
      return
    }
    setBusy(true)
    const result = await save({ [name]: value })
    setBusy(false)
    if (result.ok) {
      setErrors({})
      setMessage(null)
      setSaved(true)
    } else {
      const prefix = `${name}.`
      setErrors(Object.fromEntries(Object.entries(result.fields).map(([key, text]) => [key.startsWith(prefix) ? key.slice(prefix.length) : key, text])))
      setMessage(result.message)
    }
  }
  return { busy, saved, errors, message, run }
}

// ------------------------------------------------------------------ drain
export function DrainForm({ value, save, photoLine, onPick, picking }: {
  value?: DrainLocation
  save: Save
  photoLine: LonLat[]
  onPick: () => void
  picking: boolean
}) {
  const [point, setPoint] = useState(formatLatLon(value?.point))
  const [tolerance, setTolerance] = useState(String(value?.toleranceM ?? 30))
  const [name, setName] = useState(value?.name ?? '')
  const [length, setLength] = useState(value?.lengthM?.toString() ?? '')
  const [width, setWidth] = useState(value?.widthM?.toString() ?? '')
  const [depth, setDepth] = useState(value?.depthM?.toString() ?? '')
  const saver = useSaver(save, 'drainLocation')

  function submit(extra: Partial<DrainLocation> = {}) {
    const errors: Record<string, string> = {}
    const parsed = extra.point ?? parseLatLon(point)
    if (!parsed && !extra.line && !value?.line) errors.point = 'Enter "latitude, longitude", e.g. 22.5801, 88.4712'
    const numbers = { toleranceM: optionalNumber(tolerance), lengthM: optionalNumber(length), widthM: optionalNumber(width), depthM: optionalNumber(depth) }
    for (const [key, number] of Object.entries(numbers)) if (number === null) errors[key] = 'Must be a number'
    const body: Record<string, unknown> = {
      point: parsed ?? undefined,
      toleranceM: numbers.toleranceM ?? 30,
      source:
        extra.source ??
        (value?.line
          ? value.source
          : value?.source === 'map_selected' && formatLatLon(value.point) === point.trim()
            ? 'map_selected'
            : 'manual_point'),
      derivedFromPhotos: extra.derivedFromPhotos ?? (value?.line ? value.derivedFromPhotos : false),
      name: name.trim() || null,
      lengthM: numbers.lengthM, widthM: numbers.widthM, depthM: numbers.depthM,
    }
    const line = extra.line ?? value?.line
    if (line) body.line = line
    saver.run(body, errors)
  }

  return (
    <form className="jt-form" onSubmit={(event) => { event.preventDefault(); submit() }}>
      <div className="jt-row">
        <label className="jt-field grow">
          <span>Drain point (latitude, longitude)</span>
          <input value={point} onChange={(event) => setPoint(event.target.value)} placeholder="22.5801, 88.4712" inputMode="decimal" />
          <FieldError errors={saver.errors} name="point" />
        </label>
        <button type="button" className={`btn btn-quiet jt-pick${picking ? ' is-on' : ''}`} onClick={onPick} aria-pressed={picking}>
          {picking ? 'Click the map…' : 'Pick on map'}
        </button>
      </div>
      <div className="jt-row">
        <label className="jt-field">
          <span>Tolerance, m</span>
          <input value={tolerance} onChange={(event) => setTolerance(event.target.value)} inputMode="decimal" />
          <FieldError errors={saver.errors} name="toleranceM" />
        </label>
        <label className="jt-field grow">
          <span>Name (optional)</span>
          <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
        </label>
      </div>
      <details className="jt-more">
        <summary>Dimensions for the volume check (optional)</summary>
        <div className="jt-row">
          {([['Length, m', length, setLength, 'lengthM'], ['Width, m', width, setWidth, 'widthM'], ['Depth, m', depth, setDepth, 'depthM']] as const).map(([text, current, set, key]) => (
            <label className="jt-field" key={key}>
              <span>{text}</span>
              <input value={current} onChange={(event) => set(event.target.value)} inputMode="decimal" />
              <FieldError errors={saver.errors} name={key} />
            </label>
          ))}
        </div>
      </details>
      {value?.line && (
        <p className="jt-note">
          Saved line: {value.line.length} points, {value.source === 'field_approximate' ? 'field-approximated' : 'supplied'}
          {value.derivedFromPhotos ? ', drawn from the photos themselves (so R1 will be inconclusive)' : ''}.{' '}
          <button type="button" className="link" onClick={() => saver.run({ ...value, line: undefined, source: 'manual_point', derivedFromPhotos: false })}>Remove line</button>
        </p>
      )}
      {photoLine.length >= 2 && !value?.line && (
        <p className="jt-note">
          <button type="button" className="link" onClick={() => submit({ line: photoLine, point: photoLine[Math.floor(photoLine.length / 2)], source: 'field_approximate', derivedFromPhotos: true })}>
            Approximate the channel from the {photoLine.length} photo GPS fixes
          </button>{' '}
          — labelled field-approximate, and circular for checking those same photos.
        </p>
      )}
      <p className="jt-note">A supplied location is never an official drain map. Checks against it say “consistent”, not “pass”.</p>
      <SaveRow busy={saver.busy} saved={saver.saved} message={saver.message} onClear={value ? () => saver.run(null) : undefined} />
    </form>
  )
}

// --------------------------------------------------------------- disposal
export function DisposalForm({ value, save, onPick, picking }: {
  value?: DisposalSite; save: Save; onPick: () => void; picking: boolean
}) {
  const [point, setPoint] = useState(formatLatLon(value?.point))
  const [radius, setRadius] = useState(String(value?.radiusM ?? 150))
  const [name, setName] = useState(value?.name ?? '')
  const saver = useSaver(save, 'disposalSite')

  return (
    <form
      className="jt-form"
      onSubmit={(event) => {
        event.preventDefault()
        const errors: Record<string, string> = {}
        const parsed = parseLatLon(point)
        const radiusM = optionalNumber(radius)
        if (!parsed) errors.point = 'Enter "latitude, longitude"'
        if (radiusM === null || radiusM === undefined) errors.radiusM = 'Must be a number'
        saver.run({ point: parsed, radiusM, name: name.trim() || null, polygon: value?.polygon }, errors)
      }}
    >
      <div className="jt-row">
        <label className="jt-field grow">
          <span>Site centre (latitude, longitude)</span>
          <input value={point} onChange={(event) => setPoint(event.target.value)} placeholder="22.5600, 88.4300" inputMode="decimal" />
          <FieldError errors={saver.errors} name="point" />
        </label>
        <button type="button" className={`btn btn-quiet jt-pick${picking ? ' is-on' : ''}`} onClick={onPick} aria-pressed={picking}>
          {picking ? 'Click the map…' : 'Pick on map'}
        </button>
      </div>
      <div className="jt-row">
        <label className="jt-field">
          <span>Geofence radius, m</span>
          <input value={radius} onChange={(event) => setRadius(event.target.value)} inputMode="decimal" />
          <FieldError errors={saver.errors} name="radiusM" />
        </label>
        <label className="jt-field grow">
          <span>Name (optional)</span>
          <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
        </label>
      </div>
      <p className="jt-note">Designated for this trial only. SiltProof does not know whether it is an authorised site, and no check says it is.</p>
      <SaveRow busy={saver.busy} saved={saver.saved} message={saver.message} onClear={value ? () => saver.run(null) : undefined} />
    </form>
  )
}

// ------------------------------------------------------------------ claim
export function ClaimForm({ value, save }: { value?: Claim; save: Save }) {
  const [quantity, setQuantity] = useState(value?.quantityTonnes?.toString() ?? '')
  const [rate, setRate] = useState(value?.ratePerTonne?.toString() ?? '')
  const [amount, setAmount] = useState(value?.amountRupees?.toString() ?? '')
  const [contractor, setContractor] = useState(value?.contractor ?? '')
  const [reference, setReference] = useState(value?.reference ?? '')
  const saver = useSaver(save, 'claim')
  const numbers = { quantityTonnes: optionalNumber(quantity), ratePerTonne: optionalNumber(rate), amountRupees: optionalNumber(amount) }
  const hint = claimArithmetic(numbers.quantityTonnes ?? undefined, numbers.ratePerTonne ?? undefined, numbers.amountRupees ?? undefined)

  return (
    <form
      className="jt-form"
      onSubmit={(event) => {
        event.preventDefault()
        const errors: Record<string, string> = {}
        for (const [key, number] of Object.entries(numbers)) if (number === null) errors[key] = 'Must be a number'
        saver.run({ ...numbers, contractor: contractor.trim() || null, reference: reference.trim() || null }, errors)
      }}
    >
      <div className="jt-row">
        <label className="jt-field">
          <span>Claimed quantity, t</span>
          <input value={quantity} onChange={(event) => setQuantity(event.target.value)} inputMode="decimal" />
          <FieldError errors={saver.errors} name="quantityTonnes" />
        </label>
        <label className="jt-field">
          <span>Rate, ₹ per tonne</span>
          <input value={rate} onChange={(event) => setRate(event.target.value)} inputMode="decimal" />
          <FieldError errors={saver.errors} name="ratePerTonne" />
        </label>
        <label className="jt-field">
          <span>Claimed amount, ₹</span>
          <input value={amount} onChange={(event) => setAmount(event.target.value)} inputMode="decimal" />
          <FieldError errors={saver.errors} name="amountRupees" />
        </label>
      </div>
      <div className="jt-row">
        <label className="jt-field grow">
          <span>Contractor or reference name</span>
          <input value={contractor} maxLength={120} onChange={(event) => setContractor(event.target.value)} />
        </label>
        <label className="jt-field grow">
          <span>Bill or contract reference</span>
          <input value={reference} maxLength={80} onChange={(event) => setReference(event.target.value)} />
        </label>
      </div>
      {hint && <p className="jt-callout warn">{hint}</p>}
      <SaveRow busy={saver.busy} saved={saver.saved} message={saver.message} onClear={value ? () => saver.run(null) : undefined} />
    </form>
  )
}

// ---------------------------------------------------------- work window
export function WindowForm({ value, save }: { value?: WorkWindow; save: Save }) {
  const [start, setStart] = useState(value?.start ?? '')
  const [end, setEnd] = useState(value?.end ?? '')
  const saver = useSaver(save, 'workWindow')
  return (
    <form className="jt-form" onSubmit={(event) => { event.preventDefault(); saver.run({ start: start.trim(), end: end.trim() }) }}>
      <div className="jt-row">
        <label className="jt-field grow">
          <span>Work window start</span>
          <input value={start} onChange={(event) => setStart(event.target.value)} placeholder="2026-10-09T00:00:00+05:30" />
          <FieldError errors={saver.errors} name="start" />
        </label>
        <label className="jt-field grow">
          <span>Work window end</span>
          <input value={end} onChange={(event) => setEnd(event.target.value)} placeholder="2026-10-09T23:59:59+05:30" />
          <FieldError errors={saver.errors} name="end" />
        </label>
      </div>
      <p className="jt-note">ISO 8601 with a timezone offset. Photos are dated from their EXIF; many phones record no timezone, which the check will say.</p>
      <SaveRow busy={saver.busy} saved={saver.saved} message={saver.message} onClear={value ? () => saver.run(null) : undefined} />
    </form>
  )
}

// ------------------------------------------------------------------ truck
export function TruckForm({ value, save }: { value?: Truck; save: Save }) {
  const [vehicle, setVehicle] = useState(value?.vehicleNo ?? '')
  const [capacity, setCapacity] = useState(value?.capacityTonnes?.toString() ?? '')
  const saver = useSaver(save, 'truck')
  return (
    <form
      className="jt-form"
      onSubmit={(event) => {
        event.preventDefault()
        const capacityTonnes = optionalNumber(capacity)
        saver.run({ vehicleNo: vehicle.trim() || null, capacityTonnes: capacityTonnes ?? undefined },
          capacityTonnes === null ? { capacityTonnes: 'Must be a number' } : {})
      }}
    >
      <div className="jt-row">
        <label className="jt-field grow">
          <span>Truck registration</span>
          <input value={vehicle} maxLength={20} onChange={(event) => setVehicle(event.target.value)} placeholder="WB 25 AB 1234" />
          <FieldError errors={saver.errors} name="vehicleNo" />
        </label>
        <label className="jt-field">
          <span>Rated capacity, t</span>
          <input value={capacity} onChange={(event) => setCapacity(event.target.value)} inputMode="decimal" />
          <FieldError errors={saver.errors} name="capacityTonnes" />
        </label>
      </div>
      <SaveRow busy={saver.busy} saved={saver.saved} message={saver.message} onClear={value ? () => saver.run(null) : undefined} />
    </form>
  )
}
