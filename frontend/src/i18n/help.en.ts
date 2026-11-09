// The help and contact page (issue #650). Every claim is checked against the screens and the
// backend (the PR description lists where). `{email}`, `{account}` and `{privacy}` are link slots of
// `Trans`. Never ask readers to send a password or a token.

export const helpEn = {
  'help.link': 'Help',
  'help.inline.account': 'account page',
  'help.title': 'Help and contact',
  'help.lead': 'Short answers, and how to reach us. You do not need to sign in to read this page.',

  'help.connect.title': 'Connecting your accounts',
  'help.connect.mowl.q': 'How do I connect MOWL?',
  'help.connect.mowl.a':
    'Open the Connections tab on your {account} and press Connect on MOWL. Sign in to your Intelligent Cycling (MOWL) account with your email and password. On a larger screen you may also be offered a code to scan with the MOWL app. We use your password once to get a sign-in token and keep only the token, encrypted. Disconnect at any time and we delete it. To end the sign-in on MOWL’s side, change your MOWL password.',
  'help.connect.spotify.q': 'How do I connect Spotify?',
  'help.connect.spotify.a':
    'Spotify is needed when you want us to pick songs for you. On the Connections tab, press Connect on Spotify. We guide you through making a Spotify app of your own in the Spotify Developer Dashboard, so its limits are yours. You then paste the app’s Client ID here. The person who owns the app needs Spotify Premium. Then you sign in at Spotify and allow access. We never need or ask for the app’s Client Secret.',
  'help.connect.swedebeat.q': 'Do I need Swedebeat?',
  'help.connect.swedebeat.a':
    'No, it is optional. If you teach with Swedebeat, press Connect on Swedebeat on the Connections tab. You can then design a class to one of your Swedebeat mixes, which come from your MOWL account, and match a class’s songs to Swedebeat. As with MOWL, we use the password once and keep only the token.',

  'help.design.title': 'Designing a class',
  'help.design.how.q': 'How is a class designed?',
  'help.design.how.a':
    'You describe the length, the effort and the music: your own playlist, songs we pick for you, a Swedebeat mix, or no music. An AI assistant proposes the intervals, efforts and cues. If you asked for songs, it also proposes a song list checked against MOWL’s tempo and length. Designing can take a few minutes, and you can leave the page meanwhile. The result is a suggestion, so check a class before you teach it.',
  'help.design.saved.q': 'What is saved, and when?',
  'help.design.saved.a':
    'The assistant only proposes. When you approve a song list, we create a private playlist with those songs in your Spotify and add it to your MOWL music. The workout itself goes to your MOWL account, private, only when you press Save to MOWL on the class. If you do not approve a list in time, nothing is written to your Spotify.',
  'help.design.change.q': 'Can I change a class afterwards?',
  'help.design.change.a':
    'Yes. Describe what should be different on the class page, or use Change songs to replace some of the songs. Every change makes a new version. Earlier versions stay, and you can make one of them current with Use this version. Save to MOWL again to update the workout there.',

  'help.plan.title': 'Plans and limits',
  'help.plan.limits.q': 'What limits apply to me?',
  'help.plan.limits.a':
    'Your plan sets how many classes you can design each month and how many changes a class can have. It also sets how long a class may be, how many designs can run at once, and whether games and races are included. There is also a monthly budget for the assistant’s work. Months are counted in UTC and start over on the first. The Plan and usage tab on your account page shows what you have used.',
  'help.plan.code.q': 'How do I use a code or trial?',
  'help.plan.code.a':
    'On the Plan and usage tab, type the code under Redeem a code, or press the trial button when a trial is on offer. A code can be invalid, expired or used up, and a trial can be used once.',

  'help.signin.title': 'Account and sign-in',
  'help.signin.reset.q': 'I forgot my password',
  'help.signin.reset.a':
    'Choose Forgot your password? on the sign-in page and we send a link that works for 30 minutes. A password is 12 to 128 characters. When you choose a new one, you are signed out everywhere.',
  'help.signin.methods.q': 'Sign in with Google, GitHub or Microsoft?',
  'help.signin.methods.a':
    'Yes, when they are offered on the sign-in page. You can link or unlink them under Sign-in and security on your account page. Your account uses the email address the provider has verified.',
  'help.signin.twofactor.q': 'Is there two-factor authentication?',
  'help.signin.twofactor.a':
    'Yes. Under Sign-in and security you can set up an authenticator app. It then asks for a code before sensitive changes on that page. You get recovery codes that are shown only once, so save them.',
  'help.signin.devices.q': 'Where am I signed in?',
  'help.signin.devices.a':
    'Sign-in and security lists the devices you are signed in on and the security activity of the last 30 days. You can sign out one device or everywhere. If you see something you did not do, sign out everywhere and change your password.',

  'help.data.title': 'Your data and deleting your account',
  'help.data.export.q': 'Can I get a copy of my data?',
  'help.data.export.a':
    'Yes. On the Your data tab, Download my data gives you one file with your account, classes, song lists, usage and sign-in history.',
  'help.data.delete.q': 'How do I delete my account?',
  'help.data.delete.a':
    'On the Your data tab, press Delete my account and type your email to confirm. This deletes your account, classes and song lists, plan and usage, connections and sign-in history. Jobs that are running are stopped first. It cannot be undone. Workouts saved to MOWL and playlists in your Spotify stay there, so delete them in those services if you do not want them. More in the {privacy}.',

  'help.contact.title': 'Contact',
  'help.contact.p1': 'Write to us at {email}.',
  'help.contact.include': 'When you write, please include:',
  'help.contact.none': 'No support address is available right now. Please try again later.',
  'help.contact.li1': 'The email address of your classbeat account.',
  'help.contact.li2': 'What you were doing and what you expected to happen.',
  'help.contact.li3': 'The time, and the error text if you saw one.',
  'help.contact.never':
    'Never send a password, a sign-in token, a recovery code or a one-time code. We never need them.',
  'help.contact.p2':
    'To see, correct, export or delete your data, see the {privacy}. You can do most of it yourself on your account page.',
}
