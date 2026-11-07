// The privacy notice and the terms (issue #718). Every statement here is checked against the code
// or an ADR; the PR description lists the source of each. Keys are `<doc>.<section>.<part>`: a part
// starting with `p` is a paragraph, `li` a list item (the page builds them from
// `pages/legal/structure.ts`). `{email}`, `{terms}` and `{privacy}` are link slots of `Trans`.

export const legalEn = {
  'legal.link.privacy': 'Privacy',
  'legal.link.terms': 'Terms',
  'legal.footer.label': 'Legal',
  'legal.read': 'Read the {privacy} and the {terms}.',
  'legal.inline.privacy': 'privacy notice',
  'legal.inline.terms': 'terms of use',
  'legal.updated': 'Last updated 7 November 2026.',
  'legal.contactEmail': 'support@classbeat.eu',
  'legal.controller.title': 'Who is responsible',
  'legal.controller.note':
    'These details have not been filled in yet and must be completed before the beta opens to the public.',
  'legal.controller.name': 'Controller name',
  'legal.controller.address': 'Postal address',
  'legal.controller.company': 'Company or registration number',
  'legal.controller.value': '[To be filled in by the owner]',
  'legal.controller.hosting': 'Hosting location',

  'privacy.title': 'Privacy notice',
  'privacy.lead':
    'classbeat is a private beta for indoor-cycling instructors. This page says what we keep about you, why, for how long, and what you can do about it.',

  'privacy.who.title': 'Who we are',
  'privacy.who.p1':
    'The person responsible for your data (the controller) is named below. You can write to us at {email} about anything on this page.',

  'privacy.data.title': 'What we keep',
  'privacy.data.p1': 'We keep what the service needs to work and nothing for advertising.',
  'privacy.data.li1':
    'Your account: your email address, your password stored only as a one-way hash, and which sign-in methods you use.',
  'privacy.data.li2':
    'Your sign-ins: the devices you signed in from, the time, and the IP address of a session.',
  'privacy.data.li3':
    'Your classes: what you describe, the conversation with the assistant, the songs and the finished designs with their earlier versions.',
  'privacy.data.li4':
    'Your connections to MOWL, Spotify and Swedebeat: only the sign-in token each service gave us, stored encrypted. We never store the passwords you type for MOWL or Swedebeat.',
  'privacy.data.li5':
    'Your usage: how many classes you made and the cost of the assistant for them, which sets your monthly limit.',
  'privacy.data.li6': 'Your email preferences.',

  'privacy.model.title': 'What the assistant sees',
  'privacy.model.p1':
    'To design a class we send what you wrote about it and a summary of the songs (the playlist name, and for each song its title, artist, tempo and length) to an AI model provider.',
  'privacy.model.p2':
    'We never send it your email address, name, passwords, tokens or any account or service IDs. The assistant only proposes: nothing is saved to MOWL or Spotify until you approve it.',

  'privacy.processors.title': 'Who else handles your data',
  'privacy.processors.p1':
    'We use these services to run classbeat. Each gets only what it needs for its task.',
  'privacy.processors.li1':
    'An AI model provider (Anthropic) gets the class text and song summary described above to design your class. It is based outside the EU, so this is a transfer to a third country.',
  'privacy.processors.li2':
    'MOWL, Spotify and Swedebeat receive requests only when you connect an account or save something to it. They have their own privacy notices.',
  'privacy.processors.li3':
    'If you choose to sign in with Google, GitHub or Microsoft, that provider tells us your verified email address.',
  'privacy.processors.li4':
    'Our email service sends the emails we send you (sign-up confirmation, password reset, and the like).',
  'privacy.processors.li5': 'Our hosting provider runs the servers. Its location is given in the box above.',
  'privacy.processors.li6':
    "Your browser fetches album covers directly from Spotify's image servers, which therefore see your IP address and browser. We send no referrer, no classbeat cookie and no classbeat data with that request.",
  'privacy.processors.p2': 'We do not sell your data and we do not use it for advertising.',

  'privacy.cookies.title': 'Cookies and browser storage',
  'privacy.cookies.p1':
    'We set only the cookies the site needs to sign you in and keep it safe. They are not used for tracking or advertising, so there is nothing to opt out of.',
  'privacy.cookies.li1':
    'classbeat_session: keeps you signed in, for at most 30 days, and ends after 7 days without use.',
  'privacy.cookies.li2': 'classbeat_device: recognises a device you have signed in from, for 1 year.',
  'privacy.cookies.li3': 'classbeat_csrf: protects forms against forgery, for 30 days.',
  'privacy.cookies.li4':
    'classbeat_oauth: holds the state of a Google, GitHub, Microsoft or Spotify sign-in while it runs, for 10 minutes.',
  'privacy.cookies.li5': 'classbeat_pending: holds a sign-in that still needs a step from you, for 24 hours.',
  'privacy.cookies.p2':
    'Each cookie name starts with __Host-, which means it is sent only over a secure connection to this site. We also keep your chosen language in your browser (cb-locale); it is never sent to us.',

  'privacy.retention.title': 'How long we keep it',
  'privacy.retention.li1':
    'Sign-in sessions: removed when they expire or after 7 days without use; the IP address is cleared after 30 days.',
  'privacy.retention.li2': 'Sign-in history: 30 days.',
  'privacy.retention.li3': 'Known devices: removed after 90 days without use.',
  'privacy.retention.li4': 'Security log: 7 days.',
  'privacy.retention.li5': 'Invites: removed 30 days after they are used or expire.',
  'privacy.retention.li6':
    'Your account, classes, connections and usage: until you delete them or your account.',
  'privacy.retention.p1':
    'When you delete your account everything above is deleted with it. Copies in backups of our database are kept for a limited time and then expire; deleted data can remain in a backup until then.',

  'privacy.rights.title': 'Your rights',
  'privacy.rights.p1':
    'You can see, correct, export and delete your data yourself. On your account page, Download my data gives you one file with your account, classes, song lists, usage and sign-in history, and Delete my account removes your account and its data. You can change your email address and password there too.',
  'privacy.rights.p2':
    'You also have the right to ask us to restrict how we use your data, to object to it, and to get a copy of it. Write to {email}. We answer within one month.',
  'privacy.rights.p3':
    'If you think we handle your data wrongly, you can complain to the data protection authority where you live: in Sweden the Swedish Authority for Privacy Protection (IMY), in Denmark Datatilsynet, or the authority of another EU country.',

  'privacy.changes.title': 'Changes',
  'privacy.changes.p1':
    'If we change this notice in a way that matters to you we will say so in the service. The date above shows the latest version. How you may use classbeat is described in the {terms}.',

  'terms.title': 'Terms of use',
  'terms.lead':
    'These are the short, plain rules for using the classbeat private beta. By creating an account you agree to them.',

  'terms.service.title': 'The service',
  'terms.service.p1':
    'classbeat helps indoor-cycling instructors design classes with the help of an AI assistant and, when you approve, save them to your MOWL account and create playlists on your Spotify account. It is an invite-only beta, offered as it is, and it can change or stop.',
  'terms.service.p2':
    'classbeat is not made by or affiliated with MOWL, Spotify or Swedebeat. Your use of those services is under their own terms.',

  'terms.account.title': 'Your account',
  'terms.account.li1': 'Give a real email address and keep your sign-in details to yourself.',
  'terms.account.li2': 'You are responsible for what happens under your account.',
  'terms.account.li3': 'One person, one account. Do not share your invite code.',

  'terms.content.title': 'Your classes and music',
  'terms.content.p1':
    'The classes you make are yours. You are responsible for having the right to use the music in them. We save to your accounts only what you approve, and always as private.',
  'terms.content.p2':
    'The assistant can make mistakes: songs, timings and efforts are suggestions. Check a class before you teach it.',

  'terms.use.title': 'What not to do',
  'terms.use.li1': 'Do not try to break, overload or get around the service or its limits.',
  'terms.use.li2': 'Do not use it to break the law or the terms of MOWL, Spotify or Swedebeat.',
  'terms.use.li3': "Do not use another person's account.",

  'terms.liability.title': 'Availability and liability',
  'terms.liability.p1':
    'We do our best to keep classbeat running but do not promise that it is always available or error free. To the extent the law allows, we are not liable for loss caused by using a beta service. Nothing here limits rights you have by law as a consumer.',

  'terms.ending.title': 'Ending',
  'terms.ending.p1':
    'You can stop at any time by deleting your account. We may suspend an account that breaks these terms or harms the service. What we do with your data is described in the {privacy}.',

  'terms.law.title': 'Changes and law',
  'terms.law.p1':
    'We may update these terms and will say so in the service before a change that matters to you applies. Questions go to {email}.',
  'terms.law.p2':
    'The law that governs these terms and the court that decides disputes are not yet decided and will be set here before launch.',
}
