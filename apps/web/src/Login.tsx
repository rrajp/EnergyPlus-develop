/**
 * The sign-in screen.
 *
 * It stands in front of the app, but it is not what protects it — the server
 * gates every `/api` route and the telemetry WebSocket on the same session
 * cookie. This screen only reflects that state.
 */

import { useEffect, useRef, useState } from 'react'
import { useStore } from './store'
import { IconBolt } from './ui'

export function Login() {
  const { signIn, signingIn, authError } = useStore()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const first = useRef<HTMLInputElement>(null)

  useEffect(() => { first.current?.focus() }, [])

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!signingIn) void signIn(username.trim(), password)
  }

  return (
    <div className="signin">
      <form className="signin-card" onSubmit={submit}>
        <div className="signin-mark">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="7" fill="var(--ink)" />
            <path
              d="M7 21h4.2l3.1-10 3.2 14 3.1-9H25"
              stroke="var(--chw)" strokeWidth="2.3" fill="none"
              strokeLinejoin="round" strokeLinecap="round"
            />
          </svg>
          <span>Plantworks</span>
        </div>

        <h1 className="signin-title">District plant simulator</h1>
        <p className="signin-sub">
          Sign in to design a plant and run it through EnergyPlus.
        </p>

        <label className="field">
          <span className="tag">Username</span>
          <input
            ref={first}
            className="input"
            value={username}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>

        <label className="field">
          <span className="tag">Password</span>
          <input
            className="input"
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {authError && (
          <p className="signin-error" role="alert">{authError}</p>
        )}

        <button
          className="btn btn-primary signin-submit"
          type="submit"
          disabled={signingIn || !username || !password}
        >
          <IconBolt />{signingIn ? 'Signing in…' : 'Sign in'}
        </button>

        <p className="signin-note">
          Default login is <code>admin</code> / <code>admin123</code>. Set
          {' '}<code>DHC_USERNAME</code> and <code>DHC_PASSWORD</code> before
          putting this on a public address.
        </p>
      </form>
    </div>
  )
}
