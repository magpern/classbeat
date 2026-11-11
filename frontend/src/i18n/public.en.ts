// Copy of the public pages How it works, Features and Plans (a draft), and of their links (PP4, #893). Long-form body
// text lives here, like legal.en.ts and help.en.ts. Every claim is mapped to a shipped route, flag or
// plan limit in `publicSite/claims.ts`; the Reviewer checks the wording against that table. The pages
// are drafts until PP8. Swedish and Danish are in public.sv.ts and public.da.ts (PP7).

export const publicEn = {
  'meta.howItWorks.title': 'How it works | classbeat',
  'meta.howItWorks.description':
    'Describe a class, refine it by chatting, then approve it and save it to MOWL. See what you connect.',
  'meta.features.title': 'Features | classbeat',
  'meta.features.description':
    'What classbeat does for indoor-cycling instructors, and what each feature needs: a connection, your plan or a switch.',

  'meta.pricing.title': 'Plans | classbeat',
  'meta.pricing.description':
    'What each classbeat plan includes: classes per month, class length, games, FTP tests and design quality. Plans are not open yet.',

  'pub.nav.label': 'Pages',
  'pub.nav.features': 'Features',
  'pub.cta.line': 'Sign-up is by invitation.',

  'claims.class.available': 'Available',
  'claims.class.connection': 'Needs a connection',
  'claims.class.gated': 'Depends on plan or switch',
  'claims.note.available': 'Included with your account.',
  'claims.note.mowl': 'Needs your MOWL account connected.',
  'claims.note.songPicking': 'Needs a Spotify connection with your own Client ID, and song picking on your plan. Available once we switch it on.',
  'claims.note.swedebeat': 'Needs your Swedebeat account connected. Available once we switch it on.',
  'claims.note.games':
    'Needs a plan that includes games. MOWL games need a commercial MOWL licence, which we do not check.',
  'claims.note.switch': 'Needs your MOWL account connected. Available once we switch it on.',
  'claims.note.plan': 'Set by your plan. The Plan and usage tab on your account page shows what you have used.',

  'how.title': 'How classbeat works',
  'how.lead':
    'Describe the class you want to teach. We design it, you refine it, and no workout reaches MOWL until you approve it.',
  'how.steps.title': 'From request to workout',
  'how.step.describe.title': 'Describe',
  'how.step.describe.text':
    'Write the class in your own words: length, effort, climbs, sprints, a game, the music. We choose what you leave out and show you what we assumed.',
  'how.step.generate.title': 'Generate',
  'how.step.generate.text':
    'We design the class from warm-up to cool-down and draw it as a profile. It can take a few minutes, and you can leave the page meanwhile.',
  'how.step.refine.title': 'Refine',
  'how.step.refine.text':
    'Ask for changes in the chat, such as a longer cool-down or an easier second climb. Every change is a new version, and earlier versions stay.',
  'how.step.save.title': 'Approve and save',
  'how.step.save.text':
    'When you are happy, press Save to MOWL. The class becomes a private workout in your MOWL account. Until then, no workout is written to MOWL.',
  'how.connect.title': 'What you connect',
  'how.connect.lead': 'You connect accounts on your account page. We never keep your MOWL or Swedebeat password.',
  'how.connect.mowl.name': 'MOWL',
  'how.connect.mowl.text': 'Required. We read your music from it, and save your classes to it.',
  'how.connect.spotify.name': 'Spotify',
  'how.connect.spotify.text':
    'Optional. Only if you want us to pick songs for you. You make a Spotify app of your own, so its limits are yours. Its owner needs Spotify Premium.',
  'how.connect.swedebeat.name': 'Swedebeat',
  'how.connect.swedebeat.text': 'Optional. Only if you teach with Swedebeat, to design a class to one of your mixes.',

  'feat.title': 'What you can do with classbeat',
  'feat.lead': 'Each feature says what it needs: your account, a connection, or the right plan or switch.',

  'feat.group.request': 'Describe it your way',
  'feat.group.music': 'Music that fits the class',
  'feat.group.shape': 'Shape the class',
  'feat.group.control': 'Stay in control',

  'feat.describe.title': 'Write the class in your own words',
  'feat.describe.text': 'Say what matters and leave out the rest. We fill the gaps.',
  'feat.assumptions.title': 'See how we read your request',
  'feat.assumptions.text': 'Each point is marked as you asked, assumed by us, or not possible as asked.',
  'feat.advanced.title': 'Set every detail yourself',
  'feat.advanced.text': 'Prefer a form? Advanced lets you choose each detail.',

  'feat.playlist.title': 'Design to your own playlist',
  'feat.playlist.text': 'Choose a playlist from your MOWL music, and the class is timed to its songs.',
  'feat.songs.title': 'Let us pick the songs',
  'feat.songs.text':
    'Ask for songs and approve the list first. The songs you approve go into a private playlist in your Spotify.',
  'feat.swedebeat.title': 'Design to a Swedebeat mix',
  'feat.swedebeat.text': 'Time the class to one of your Swedebeat mixes.',
  'feat.noMusic.title': 'Classes without music',
  'feat.noMusic.text': 'Get intervals and cues, and play your own music.',

  'feat.profile.title': 'See the class before you teach it',
  'feat.profile.text': 'The class is drawn as a profile of zones, with its segments, length and TSS.',
  'feat.chat.title': 'Change it by chatting',
  'feat.chat.text': 'Say what should be different. Every change is a new version.',
  'feat.versions.title': 'Compare and return to versions',
  'feat.versions.text':
    'Compare two versions, make an earlier one current, or duplicate a class to try a variation.',
  'feat.games.title': 'Add a MOWL game',
  'feat.games.text': 'Ask for Racetrack, Power Pull, Fast Lane or Overflow in a cycling class.',
  'feat.running.title': 'Run classes too',
  'feat.running.text': 'Design a running class to a playlist from your MOWL music. Running classes have no games.',

  'feat.save.title': 'Save to MOWL when you decide',
  'feat.save.text':
    'Press Save to MOWL and the class becomes a private workout in your account. Update it later from the same page.',
  'feat.limits.title': 'Limits that fit your plan',
  'feat.limits.text':
    'Your plan sets how many classes you design each month, how many changes a class can have, and how long a class can be.',
  'feat.data.title': 'Your data stays yours',
  'feat.data.text': 'Export your data, or delete your account, from your account page.',
  'feat.languages.title': 'Use it in your language',
  'feat.languages.text': 'The site speaks English, Swedish and Danish. The classes are written in English.',

  'pricing.title': 'Plans',
  'pricing.lead': 'Three plans, from a small free allowance to a lot of classes. Plans are not open for purchase yet.',
  'pricing.plans.title': 'What each plan includes',
  'pricing.free.blurb': 'Try classbeat with a small allowance every month.',
  'pricing.instructor.blurb': 'For instructors who teach every week.',
  'pricing.pro.blurb': 'For instructors who design a lot of classes.',
  'pricing.quality.note': 'Better and Best give more careful designs.',
  'pricing.same.title': 'The same on every plan',
  'pricing.same.races': 'Races, the short hard bursts of effort.',
  'pricing.same.classes': 'Running classes, your class history, and export and delete of your data.',
  'pricing.same.connections': 'Your MOWL, Spotify and Swedebeat connections.',
  'pricing.same.songs':
    'Song picking is the same on every plan, and needs a Spotify connection with your own Client ID once we switch it on.',
} as const
