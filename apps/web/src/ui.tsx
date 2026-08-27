/** Form and layout primitives. Every label is drafting annotation. */

import type { ReactNode } from 'react'

export function Card({
  title, aside, children, flush, style,
}: {
  title?: string; aside?: ReactNode; children: ReactNode
  flush?: boolean; style?: React.CSSProperties
}) {
  return (
    <section className="card" style={style}>
      {title && (
        <header className="card-head">
          <h2 className="card-title">{title}</h2>
          {aside}
        </header>
      )}
      <div className={`card-body${flush ? ' flush' : ''}`}>{children}</div>
    </section>
  )
}

export function Field({
  label, unit, hint, children,
}: {
  label: string; unit?: string; hint?: string; children: ReactNode
}) {
  return (
    <label className="field">
      <span className="tag">
        {label}
        {unit && <span className="field-unit">{unit}</span>}
      </span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  )
}

export function NumberInput({
  value, onChange, min, max, step = 'any', disabled,
}: {
  value: number
  onChange: (v: number) => void
  min?: number; max?: number; step?: number | 'any'; disabled?: boolean
}) {
  return (
    <input
      type="number" className="input num" value={Number.isFinite(value) ? value : ''}
      min={min} max={max} step={step} disabled={disabled}
      onChange={(e) => {
        const v = e.target.valueAsNumber
        if (Number.isFinite(v)) onChange(v)
      }}
    />
  )
}

export function TextInput({
  value, onChange, placeholder,
}: {
  value: string; onChange: (v: string) => void; placeholder?: string
}) {
  return (
    <input
      className="input" value={value} placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

export function Select<T extends string>({
  value, onChange, options, disabled,
}: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: string }[]
  disabled?: boolean
}) {
  return (
    <select
      className="select" value={value} disabled={disabled}
      onChange={(e) => onChange(e.target.value as T)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  )
}

export function Switch({
  checked, onChange, label,
}: {
  checked: boolean; onChange: (v: boolean) => void; label: string
}) {
  return (
    <label className="switch">
      <input
        type="checkbox" checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="switch-track" />
      <span>{label}</span>
    </label>
  )
}

export function Segmented<T extends string>({
  value, onChange, options,
}: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string }[]
}) {
  return (
    <div className="seg" role="group">
      {options.map((o) => (
        <button
          key={o.value} type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Readout({
  label, value, unit,
}: {
  label: string; value: string | number; unit?: string
}) {
  return (
    <div className="readout">
      <span style={{ color: 'var(--muted)' }}>{label}</span>
      <span className="readout-value">
        {value}{unit && <span style={{ color: 'var(--muted)', fontWeight: 400 }}> {unit}</span>}
      </span>
    </div>
  )
}

export function Kpi({
  label, value, unit, sub,
}: {
  label: string; value: string; unit?: string; sub?: string
}) {
  return (
    <div className="kpi">
      <div className="tag">{label}</div>
      <div className="kpi-value">
        {value}
        {unit && <span className="kpi-unit">{unit}</span>}
      </div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

export function Empty({
  title, children,
}: {
  title: string; children?: ReactNode
}) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {children && <p className="hint" style={{ maxWidth: 340 }}>{children}</p>}
    </div>
  )
}

/* ---- icons: 16px, 1.6 stroke, single path where possible --------------- */

const ico = (d: string) => function Icon({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  )
}

export const IconPlay = ico('M4.5 3.2v9.6l8-4.8z')
export const IconPause = ico('M5.5 3.5v9M10.5 3.5v9')
export const IconStop = ico('M4.5 4.5h7v7h-7z')
export const IconCheck = ico('M3 8.5l3.2 3.2L13 5')
export const IconWarn = ico('M8 2.2l6 11.6H2zM8 6.4v3.1M8 11.6v.01')
export const IconInfo = ico('M8 7.2v4.4M8 4.6v.01M8 14.5A6.5 6.5 0 108 1.5a6.5 6.5 0 000 13z')
export const IconX = ico('M4 4l8 8M12 4l-8 8')
export const IconPlus = ico('M8 3.5v9M3.5 8h9')
export const IconCopy = ico('M5.5 5.5V3.2h7.3v7.3H10.5M3.2 5.5h7.3v7.3H3.2z')
export const IconTrash = ico('M2.8 4.6h10.4M6 4.6V2.9h4v1.7M4.4 4.6l.7 8.5h5.8l.7-8.5')
export const IconDownload = ico('M8 2.5v7.8M4.8 7.5L8 10.7l3.2-3.2M2.8 13.2h10.4')
export const IconReset = ico('M13 8a5 5 0 11-1.8-3.8M13 1.8v3.2h-3.2')
export const IconUpload = ico('M8 10.8V3M4.8 6.2L8 3l3.2 3.2M2.8 13.2h10.4')
export const IconBolt = ico('M9.2 1.8L3.5 9.2h3.4l-.7 5 5.6-7.4H8.4z')
export const IconSun = ico('M8 11.2a3.2 3.2 0 100-6.4 3.2 3.2 0 000 6.4zM8 1.5v1.4M8 13.1v1.4M2.5 8H1.1M14.9 8h-1.4M4.1 4.1l-1-1M12.9 12.9l-1-1M4.1 11.9l-1 1M12.9 3.1l-1 1')
export const IconSignOut = ico('M6.2 13.2H3.4V2.8h2.8M10.2 11.2L13.4 8l-3.2-3.2M13.4 8H6.6')
export const IconMoon = ico('M13.4 9.6A5.8 5.8 0 016.4 2.6a5.8 5.8 0 107 7z')

export function SeverityIcon({ severity }: { severity: 'info' | 'warning' | 'error' }) {
  if (severity === 'error') return <IconWarn />
  if (severity === 'warning') return <IconWarn />
  return <IconInfo />
}
