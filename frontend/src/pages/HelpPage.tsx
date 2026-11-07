import { Fragment } from 'react'
import { SupportLink, useSupportAddress } from '../components/SupportLink'
import { Trans, useT } from '../i18n/I18n'
import { Link } from '../router/Router'
import { CONTACT_PARTS, HELP_TOPICS, helpKey } from './help/structure'

// The part after which the list of things to include follows.
const INCLUDE = CONTACT_PARTS[1]

/** Help and contact (issue #650): public, in the visitor's language, no form and no tracking. */
export function HelpPage() {
  const t = useT()
  const address = useSupportAddress()
  const slots = {
    account: <Link to="/account">{t('help.inline.account')}</Link>,
    privacy: <Link to="/privacy">{t('legal.inline.privacy')}</Link>,
    email: <SupportLink address={address} />,
  }
  return (
    <article className="mx-auto flex max-w-3xl flex-col gap-8" aria-labelledby="help-title">
      <header className="flex flex-col gap-3">
        <h1 id="help-title" className="m-0 font-display text-4xl font-extrabold leading-tight tracking-tight">
          {t('help.title')}
        </h1>
        <p className="m-0 text-lg">{t('help.lead')}</p>
      </header>
      {HELP_TOPICS.map((topic) => (
        <section key={topic.id} aria-labelledby={`help-${topic.id}`} className="flex flex-col gap-3">
          <h2 id={`help-${topic.id}`} className="m-0 font-display text-2xl font-bold">
            {t(helpKey(topic.id, 'title'))}
          </h2>
          {topic.questions.map((q) => (
            <Fragment key={q}>
              <h3 className="m-0 mt-2 font-display text-lg font-bold">{t(helpKey(topic.id, `${q}.q`))}</h3>
              <p className="m-0">
                <Trans id={helpKey(topic.id, `${q}.a`)} slots={slots} />
              </p>
            </Fragment>
          ))}
        </section>
      ))}
      <section aria-labelledby="help-contact" className="flex flex-col gap-3">
        <h2 id="help-contact" className="m-0 font-display text-2xl font-bold">
          {t('help.contact.title')}
        </h2>
        <ContactBody slots={slots} address={address} />
      </section>
    </article>
  )
}

function ContactBody({
  slots,
  address,
}: {
  slots: Parameters<typeof Trans>[0]['slots']
  address: string | null | undefined
}) {
  const t = useT()
  const items = CONTACT_PARTS.filter((part) => part.startsWith('li'))
  return (
    <>
      {CONTACT_PARTS.filter((part) => !part.startsWith('li')).map((part) => (
        <Fragment key={part}>
          {part === 'p1' && address === null ? (
            <p className="m-0">{t('help.contact.none')}</p>
          ) : (
            <p className="m-0">
              <Trans id={helpKey('contact', part)} slots={slots} />
            </p>
          )}
          {part === INCLUDE && (
            <ul className="m-0 flex flex-col gap-2 pl-6">
              {items.map((item) => (
                <li key={item}>
                  <Trans id={helpKey('contact', item)} slots={slots} />
                </li>
              ))}
            </ul>
          )}
        </Fragment>
      ))}
    </>
  )
}
