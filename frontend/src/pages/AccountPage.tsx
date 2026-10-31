import { useEffect, useState } from 'react'
import { logout, logoutAll } from '../api/auth'
import { oauthOutcomeMessage } from '../api/messages'
import { useSession } from '../auth/SessionContext'
import { Connections } from '../components/account/Connections'
import { ChangeEmail, ChangePassword } from '../components/account/Credentials'
import { DeleteAccount } from '../components/account/DeleteAccount'
import { Devices } from '../components/account/Devices'
import { ExportData } from '../components/account/ExportData'
import { Plan } from '../components/account/Plan'
import { SignInMethods } from '../components/account/SignInMethods'
import { SpotifySummary } from '../components/account/Spotify'
import { TwoFactor } from '../components/account/TwoFactor'
import { Usage } from '../components/account/Usage'
import { Notice, Section, useAction } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { Button } from '../components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs'
import { useQuery, useRouter } from '../router/Router'
import { ThemeSwitch } from '../theme/ThemeSwitch'

const OAUTH_NOTES: Record<string, string> = {
  linked: 'The sign-in method is linked to your account.',
  reauthed: 'Thanks, you are confirmed. Repeat the change you were making.',
}

const ACCOUNT_TABS = [
  { id: 'connections', label: 'Connections' },
  { id: 'plan', label: 'Plan and usage' },
  { id: 'security', label: 'Sign-in and security' },
  { id: 'data', label: 'Your data' },
  { id: 'appearance', label: 'Appearance' },
] as const

type TabId = (typeof ACCOUNT_TABS)[number]['id']

function isTab(value: string): value is TabId {
  return ACCOUNT_TABS.some((tab) => tab.id === value)
}

/**
 * The open tab lives in the address's fragment (`/account#security`), so a link or a reload
 * keeps it and the query string (OAuth outcomes, return addresses) is untouched. Coming back
 * from a provider sign-in (`?oauth=`) opens Sign-in and security, where it started.
 */
function initialTab(oauth: string | null): TabId {
  const hash = window.location.hash.replace(/^#/, '')
  if (isTab(hash)) return hash
  return oauth !== null ? 'security' : 'connections'
}

export function AccountPage() {
  return (
    <RequireAuth>
      <Account />
    </RequireAuth>
  )
}

function initial(email: string): string {
  const first = [...email.trim()][0]
  return first === undefined ? '?' : first.toUpperCase()
}

/**
 * Account (ADR 0013 section 5): a profile header with Change email, then five tabs. A restyle:
 * every section keeps its requests, its recent-authentication and step-up prompts (the guard)
 * and its confirmations.
 */
function Account() {
  const { session, refresh } = useSession()
  const { navigate } = useRouter()
  const outcome = useQuery().get('oauth')
  const [tab, setTab] = useState<TabId>(() => initialTab(outcome))
  const action = useAction()
  const email = session?.user?.email ?? ''
  const name = email.includes('@') ? email.slice(0, email.indexOf('@')) : email

  const signOut = (everywhere: boolean) => {
    void action.run(async () => {
      await (everywhere ? logoutAll() : logout())
      await refresh()
      navigate('/signin')
    })
  }

  // A fragment-only navigation (a link to `/account#plan`, the browser's back button) changes
  // the tab without remounting the page.
  useEffect(() => {
    const follow = () => {
      const hash = window.location.hash.replace(/^#/, '')
      if (isTab(hash)) setTab(hash)
    }
    window.addEventListener('hashchange', follow)
    return () => {
      window.removeEventListener('hashchange', follow)
    }
  }, [])

  const choose = (value: string) => {
    if (!isTab(value)) return
    setTab(value)
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#${value}`)
  }

  return (
    <div className="flex flex-col gap-6 text-text">
      <header className="flex flex-col gap-4 rounded-lg border border-line bg-surface p-5 sm:flex-row sm:items-start">
        <span
          aria-hidden="true"
          className="inline-flex size-16 shrink-0 items-center justify-center rounded-full bg-brand font-display text-2xl font-bold text-on-brand"
        >
          {initial(email)}
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <h1 className="m-0 text-sm font-medium tracking-wide text-muted uppercase">Your account</h1>
          <p className="m-0 font-display text-2xl font-bold break-words">{name}</p>
          <p className="m-0 break-words text-muted" data-testid="account-email">
            {email}
          </p>
          <ChangeEmail current={email} />
        </div>
      </header>
      {outcome ? (
        Object.hasOwn(OAUTH_NOTES, outcome) ? (
          <Notice kind="success">{OAUTH_NOTES[outcome]}</Notice>
        ) : (
          <Notice kind="error">{oauthOutcomeMessage(outcome)}</Notice>
        )
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <Tabs value={tab} onValueChange={choose}>
        <TabsList aria-label="Account">
          {ACCOUNT_TABS.map((item) => (
            <TabsTrigger key={item.id} value={item.id}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="connections" className="flex flex-col gap-4">
          <Connections />
          <SpotifySummary />
          <Section title="Swedebeat">
            <p className="text-muted">Coming later.</p>
          </Section>
        </TabsContent>
        <TabsContent value="plan" className="flex flex-col gap-4">
          <Plan />
          <details className="rounded-lg border border-line bg-surface p-4">
            <summary className="min-h-11 cursor-pointer content-center font-medium">Show usage details</summary>
            <Usage />
          </details>
        </TabsContent>
        <TabsContent value="security" className="flex flex-col gap-4">
          <SignInMethods />
          <ChangePassword />
          <TwoFactor />
          <Devices
            busy={action.busy}
            onSignOutEverywhere={() => {
              signOut(true)
            }}
          />
        </TabsContent>
        <TabsContent value="data" className="flex flex-col gap-4">
          <Section title="Download my data">
            <ExportData />
          </Section>
          <Section title="Delete my account">
            <DeleteAccount email={email} />
          </Section>
          <Section title="Sign out">
            <p>
              <Button
                variant="secondary"
                disabled={action.busy}
                onClick={() => {
                  signOut(false)
                }}
              >
                Sign out
              </Button>
            </p>
          </Section>
        </TabsContent>
        <TabsContent value="appearance" className="flex flex-col gap-4">
          <Section title="Theme">
            <p className="text-muted">Dark is the default. Same as device follows your phone or computer.</p>
            <ThemeSwitch />
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  )
}
