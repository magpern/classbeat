// DRAFT legal pages (PB-4, #975, ADR 0032 section 6): terms, privacy, refunds and sub-processors.
// The text is placeholder wording for the owner and a lawyer. It states only what the code or an ADR
// says (the source is named in the PR description) and marks every open point as "[Owner to decide]".
// Keys are `draft.<doc>.<section>.<part>`: a part starting with `p` is a paragraph, `li` a list item
// (the page is built from `pages/legalDraft/structure.ts`). English only; the keys are on
// `untranslated.json` until the owner publishes the pages (ADR 0023 section 8).

export const legalDraftEn = {
  'draft.banner.title': 'Draft: not in force',
  'draft.banner.body':
    'This page is a draft: placeholder text for the owner and a lawyer to review. It is not published, not legal advice and does not bind anyone. Items in square brackets are open decisions.',

  'meta.draft.terms.title': 'Draft terms of use | classbeat',
  'meta.draft.terms.description':
    'Draft terms of use for classbeat, placeholder text for the owner and a lawyer. Not published and not in force.',
  'draft.terms.title': 'Terms of use (draft)',
  'draft.terms.lead':
    'A draft of the rules for using classbeat once plans can be bought. It builds on the terms of the private beta and is not in force.',

  'draft.terms.service.title': 'The service',
  'draft.terms.service.p1':
    'classbeat helps indoor-cycling instructors design classes with an AI assistant and, when you approve, save them to your MOWL account and create playlists on your Spotify account.',
  'draft.terms.service.p2':
    'classbeat is not made by or affiliated with MOWL, Spotify or Swedebeat. Your use of those services is under their own terms.',
  'draft.terms.service.p3': '[Owner to decide] The legal name of the provider and its contact details.',

  'draft.terms.account.title': 'Your account',
  'draft.terms.account.li1': 'Give a real email address and keep your sign-in details to yourself.',
  'draft.terms.account.li2': 'You are responsible for what happens under your account.',
  'draft.terms.account.li3': 'One person, one account.',

  'draft.terms.plans.title': 'Plans and payment',
  'draft.terms.plans.p1':
    'classbeat has a Free plan and paid plans. Each plan sets how many classes you can make each month, the length of a class, the games and FTP tests you can use, and the design quality.',
  'draft.terms.plans.p2':
    'Paid plans are sold by Paddle, which acts as the merchant of record: Paddle takes the payment, handles VAT and sends the invoice, and classbeat never sees your card details. Paddle is not connected yet.',
  'draft.terms.plans.p3':
    'A plan runs for one billing period. A change to a higher plan applies at once; a move to a lower plan, or a cancellation, applies when the paid period ends. The rules for money paid are in the refund terms.',
  'draft.terms.plans.p4': '[Owner to decide] Prices, currencies, taxes and how a price change is announced.',

  'draft.terms.content.title': 'Your classes and music',
  'draft.terms.content.p1':
    'The classes you make are yours. You are responsible for having the right to use the music in them. We save to your accounts only what you approve, and always as private.',
  'draft.terms.content.p2':
    'The assistant can make mistakes: songs, timings and efforts are suggestions. Check a class before you teach it.',

  'draft.terms.use.title': 'What not to do',
  'draft.terms.use.li1': 'Do not try to break, overload or get around the service or its limits.',
  'draft.terms.use.li2': 'Do not use it to break the law or the terms of MOWL, Spotify or Swedebeat.',
  'draft.terms.use.li3': "Do not use another person's account.",

  'draft.terms.liability.title': 'Availability and liability',
  'draft.terms.liability.p1':
    '[Owner to decide, with a lawyer] How far availability is promised and how far liability is limited. Nothing here may limit the rights you have by law as a consumer.',

  'draft.terms.ending.title': 'Ending',
  'draft.terms.ending.p1':
    'You can stop at any time by deleting your account. We may suspend an account that breaks these terms or harms the service.',
  'draft.terms.ending.p2': '[Owner to decide] What happens to a paid period when an account is deleted or suspended.',

  'draft.terms.law.title': 'Changes and law',
  'draft.terms.law.p1': '[Owner to decide, with a lawyer] How changes to the terms are announced.',
  'draft.terms.law.p2':
    '[Owner to decide, with a lawyer] Governing law and courts. The current beta terms say Swedish law, without taking away the mandatory consumer rules of the EU or EEA country where you live.',

  'meta.draft.privacy.title': 'Draft privacy notice | classbeat',
  'meta.draft.privacy.description':
    'Draft privacy notice for classbeat, placeholder text for the owner and a lawyer. Not published and not in force.',
  'draft.privacy.title': 'Privacy notice (draft)',
  'draft.privacy.lead':
    'A draft of the privacy notice for when plans can be bought. It adds payments to the notice of the private beta and is not in force.',

  'draft.privacy.who.title': 'Who is responsible',
  'draft.privacy.who.p1':
    '[Owner to decide] Controller name, postal address, company or registration number, contact address and, if one is needed, a data protection contact.',

  'draft.privacy.data.title': 'What we keep',
  'draft.privacy.data.p1': 'We keep what the service needs to work and nothing for advertising.',
  'draft.privacy.data.li1':
    'Your account: your email address, your password as a one-way hash, the sign-in methods you use, your two-factor settings and your language.',
  'draft.privacy.data.li2': 'Your sign-ins: devices, times and the IP address of a session.',
  'draft.privacy.data.li3':
    'Your classes: what you describe, the conversation with the assistant, the songs and the finished designs with their earlier versions.',
  'draft.privacy.data.li4':
    'Your connections to MOWL, Spotify and Swedebeat: the sign-in token each service gave us, stored encrypted, and the account identifier the service reports. We never store the passwords you type for MOWL or Swedebeat.',
  'draft.privacy.data.li5': 'Your usage: how many classes you made, what the assistant cost for them, and your credits.',
  'draft.privacy.data.li6':
    'Your plan: the plan you are on, its billing periods, the amount and currency of each period, and the payment provider and its reference for your subscription.',
  'draft.privacy.data.p2':
    'We do not keep your card number: the payment provider takes the payment (see the sub-processors page).',

  'draft.privacy.basis.title': 'Why we may use it',
  'draft.privacy.basis.p1':
    '[Owner to decide, with a lawyer] The legal basis for each use, for example the agreement with you, a legitimate interest in security, and a legal duty such as bookkeeping for payments.',

  'draft.privacy.model.title': 'What the assistant sees',
  'draft.privacy.model.p1':
    'To design a class we send an AI model provider what you wrote about it, your change requests, the drafts so far and a summary of the songs. We never send it your email address, name, passwords, tokens or account IDs.',

  'draft.privacy.processors.title': 'Who else handles your data',
  'draft.privacy.processors.p1': 'The services we use, and what each one gets, are listed on the sub-processors page.',

  'draft.privacy.retention.title': 'How long we keep it',
  'draft.privacy.retention.p1':
    'Account, classes, connections and usage are kept until you delete them or your account. Sign-in sessions, sign-in history, devices and logs are kept for the periods in the current privacy notice.',
  'draft.privacy.retention.p2': '[Owner to decide, with a lawyer] How long payment records must be kept, and where.',

  'draft.privacy.rights.title': 'Your rights',
  'draft.privacy.rights.p1':
    'You can see, correct, export and delete your data yourself on your account page. [Owner to decide, with a lawyer] The wording of the other rights, how to ask, the answer time and which authority you can complain to.',

  'draft.privacy.transfers.title': 'Transfers outside the EU',
  'draft.privacy.transfers.p1':
    '[Owner to decide, with a lawyer] Which providers are outside the EU or EEA and the safeguard for each. The current notice names the AI model provider and Cloudflare Turnstile as transfers to a third country.',

  'meta.draft.refunds.title': 'Draft refund terms | classbeat',
  'meta.draft.refunds.description':
    'Draft refund terms for classbeat, placeholder text for the owner and a lawyer. Not published and not in force.',
  'draft.refunds.title': 'Refund terms (draft)',
  'draft.refunds.lead':
    'A draft of what happens to money paid for a plan. The no-refund rule below is the product rule built so far; the owner and a lawyer decide the final text.',

  'draft.refunds.rule.title': 'Draft clause: no refunds',
  'draft.refunds.rule.p1':
    'Money paid for a plan period is not paid back. When you cancel or move to a lower plan, the change applies when the paid period ends, and you keep the plan until then.',
  'draft.refunds.rule.p2':
    'When you move to a higher plan, it applies at once and you pay the difference for the rest of the period. If the higher plan is priced at or below your paid one, nothing is charged, and nothing is ever paid back.',
  'draft.refunds.rule.p3':
    'Classes you have already made in a month stay counted, whatever plan change follows.',

  'draft.refunds.law.title': 'Rights you have by law',
  'draft.refunds.law.p1':
    '[Owner to decide, with a lawyer] Whether and how the no-refund rule fits the mandatory consumer rules, such as a right to withdraw from a digital service, and what the text must say about them.',

  'draft.refunds.provider.title': 'Refunds and chargebacks at the payment provider',
  'draft.refunds.provider.p1':
    '[Owner to decide] What classbeat does with a plan when Paddle reports a refund or a chargeback. Until it is decided, such a notification is recorded as ignored with a reason and the plan does not change.',

  'draft.refunds.contact.title': 'Questions',
  'draft.refunds.contact.p1': '[Owner to decide] Where to write about a payment, and who answers.',

  'meta.draft.subprocessors.title': 'Draft sub-processors | classbeat',
  'meta.draft.subprocessors.description':
    'Draft list of the services classbeat uses to run, placeholder text for the owner and a lawyer. Not published.',
  'draft.subprocessors.title': 'Sub-processors (draft)',
  'draft.subprocessors.lead':
    'A draft list of the services that handle personal data for classbeat. It is built from the code and the decision records; the owner must confirm it before it is published.',

  'draft.subprocessors.model.title': 'AI model provider',
  'draft.subprocessors.model.p1':
    'Anthropic designs the classes. It receives the class text, change requests, drafts and a song summary (playlist name, and for each song its title, artist, tempo and length), and no email address, name, password, token or account ID. It is based outside the EU.',
  'draft.subprocessors.model.p2':
    'The code can also call OpenAI and DeepSeek, but both are switched off and no route to them is open. [Owner to decide] Whether either is ever used; if so it is added here first.',

  'draft.subprocessors.music.title': 'Your music services',
  'draft.subprocessors.music.p1':
    'MOWL, Spotify and Swedebeat receive requests through the connection you make, to read your music and to save what you approve. They act for you under your own account and have their own privacy notices.',
  'draft.subprocessors.music.p2':
    "Your browser also loads album covers directly from Spotify's image servers, which see your IP address and browser.",

  'draft.subprocessors.signin.title': 'Sign-in providers',
  'draft.subprocessors.signin.p1':
    'If you sign in with Google, GitHub or Microsoft, that provider tells us your verified email address and your account ID with it.',

  'draft.subprocessors.security.title': 'Security check',
  'draft.subprocessors.security.p1':
    'Cloudflare Turnstile checks that a person is using the sign-in, sign-up and reset forms. It sees your IP address, browser and device signals, and it is based in the US.',

  'draft.subprocessors.mail.title': 'Email',
  'draft.subprocessors.mail.p1':
    'An email service sends the mail we send you, such as the sign-up confirmation and password reset. [Owner to decide] The name and location of the email provider; the mail relay is not connected yet.',

  'draft.subprocessors.hosting.title': 'Hosting',
  'draft.subprocessors.hosting.p1':
    '[Owner to decide] The hosting provider, where the servers are, and any proxy service in front of them. The code does not name them.',

  'draft.subprocessors.payments.title': 'Payments',
  'draft.subprocessors.payments.p1':
    'Paddle is planned as the merchant of record for paid plans: it takes the payment, handles VAT and invoices, and keeps the card details. It is not connected yet. [Owner to decide] Whether Paddle is listed here as a processor or only as an independent seller, and its location.',

  'draft.subprocessors.update.title': 'Keeping this list',
  'draft.subprocessors.update.p1': '[Owner to decide, with a lawyer] How a change to this list is announced.',
}
