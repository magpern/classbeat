import { useEffect, useState } from 'react'
import { getIdentities, logout, logoutAll } from '../api/auth'
import { oauthOutcomeMessage } from '../api/messages'
import { useSession } from '../auth/SessionContext'
import type { CatalogueKey } from '../i18n/en'
import { useT } from '../i18n/I18n'
import { Connections } from '../components/account/Connections'
import { Swedebeat } from '../components/account/Swedebeat'
import { ChangeEmail, ChangePassword } from '../components/account/Credentials'
import { DeleteAccount } from '../components/account/DeleteAccount'
import { Devices } from '../components/account/Devices'
import { ExportData } from '../components/account/ExportData'
import { SecurityActivity } from '../components/account/SecurityActivity'
import { Plan } from '../components/account/Plan'
import { Passkeys } from '../components/account/Passkeys'
import { SignInMethods } from '../components/account/SignInMethods'
import { SpotifySummary } from '../components/account/Spotify'
import { TwoFactor } from '../components/account/TwoFactor'
import { Usage } from '../components/account/Usage'
import { Notice, Section, useAction } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { Button } from '../components/ui/button'
import { LanguageSetting } from '../components/account/LanguageSetting'
import { offeredLanguages } from '../components/LanguagePicker'
import { EmailPreferences } from '../components/account/EmailPreferences'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs'
import { replaceHash, useQuery, useRouter } from '../router/Router'
import { ThemeSwitch } from '../theme/ThemeSwitch'

const OAUTH_NOTES: Record<string, CatalogueKey> = {
  linked: 'account.oauth.linked',
  reauthed: 'account.oauth.reauthed',
}

const ACCOUNT_TABS = [
  { id: 'connections', label: 'account.tab.connections' },
  { id: 'plan', label: 'account.tab.plan' },
  { id: 'security', label: 'account.tab.security' },
  { id: 'data', label: 'account.tab.data' },
  { id: 'appearance', label: 'account.tab.appearance' },
] as const satisfies readonly { id: string; label: CatalogueKey }[]

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
  const t = useT()
  const { session, refresh } = useSession()
  const { navigate } = useRouter()
  const outcome = useQuery().get('oauth')
  const [tab, setTab] = useState<TabId>(() => initialTab(outcome))
  // Whether a failed provider re-authentication can point to the password (#522).
  // undefined while loading, null when it could not be read.
  const [hasPassword, setHasPassword] = useState<boolean | null | undefined>(undefined)
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

  useEffect(() => {
    if (outcome !== 'reauth_stale') return
    const controller = new AbortController()
    getIdentities(controller.signal).then(
      (state) => {
        setHasPassword(state.hasPassword)
      },
      () => {
        if (!controller.signal.aborted) setHasPassword(null)
      },
    )
    return () => {
      controller.abort()
    }
  }, [outcome])

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
    replaceHash(value)
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
          <h1 className="m-0 text-sm font-medium tracking-wide text-muted uppercase">
            {t('account.heading')}
          </h1>
          <p className="m-0 font-display text-2xl font-bold break-words">{name}</p>
          <p className="m-0 break-words text-muted" data-testid="account-email">
            {email}
          </p>
          <ChangeEmail current={email} />
        </div>
      </header>
      {outcome ? (
        Object.hasOwn(OAUTH_NOTES, outcome) ? (
          <Notice kind="success">{t(OAUTH_NOTES[outcome] ?? 'account.oauth.linked')}</Notice>
        ) : (
          <Notice kind="error">{oauthOutcomeMessage(outcome, hasPassword)}</Notice>
        )
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <Tabs value={tab} onValueChange={choose}>
        <TabsList aria-label={t('account.tabs')}>
          {ACCOUNT_TABS.map((item) => (
            <TabsTrigger key={item.id} value={item.id}>
              {t(item.label)}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="connections" className="flex flex-col gap-4">
          <Connections />
          <SpotifySummary />
          <Swedebeat />
        </TabsContent>
        <TabsContent value="plan" className="flex flex-col gap-4">
          <Plan />
          <details className="rounded-lg border border-line bg-surface p-4">
            <summary className="min-h-11 cursor-pointer content-center font-medium">
              {t('account.usageDetails')}
            </summary>
            <Usage />
          </details>
        </TabsContent>
        <TabsContent value="security" className="flex flex-col gap-4">
          <SignInMethods />
          <Passkeys />
          <ChangePassword />
          <TwoFactor />
          <Devices
            busy={action.busy}
            onSignOutEverywhere={() => {
              signOut(true)
            }}
          />
          <SecurityActivity />
        </TabsContent>
        <TabsContent value="data" className="flex flex-col gap-4">
          <Section title={t('account.email')}>
            <EmailPreferences />
          </Section>
          <Section title={t('export.button')}>
            <ExportData />
          </Section>
          <Section title={t('delete.button')}>
            <DeleteAccount email={email} />
          </Section>
          <Section title={t('account.signOut')}>
            <p>
              <Button
                variant="secondary"
                disabled={action.busy}
                onClick={() => {
                  signOut(false)
                }}
              >
                {t('account.signOut')}
              </Button>
            </p>
          </Section>
        </TabsContent>
        <TabsContent value="appearance" className="flex flex-col gap-4">
          {offeredLanguages(session?.locales ?? []).length > 1 ? (
            <Section title={t('account.language')}>
              <LanguageSetting />
            </Section>
          ) : null}
          <Section title={t('account.theme')}>
            <p className="text-muted">{t('account.themeHelp')}</p>
            <ThemeSwitch />
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  )
}
