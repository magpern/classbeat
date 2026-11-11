// The example classes of the public pages (ADR 0030, PP5). The class itself (part names, cues,
// numbers) is `publicSite/examples.json` and stays English; these are the words around it. Every
// number written in a prompt or a summary is checked against that file by `exampleCopy.test.ts`.
// No song, artist or tempo is named: the examples carry no music.

export const examplesEn = {
  // The head of the pages (the <title> and the description, ADR 0030 section 3).
  'meta.examples.title': 'Example classes | classbeat',
  'meta.examples.description':
    'Four made-up classes, each with the request it could come from, a chart and a table of every interval.',
  'meta.example.hill-challenge.title': 'Hill challenge, 45 minutes | classbeat',
  'meta.example.hill-challenge.description':
    'A made-up 45 minute ride with three climbs and a hard finish, shown as a request, a chart and a table.',
  'meta.example.90s-dance-intervals.title': '90s dance ride, 30 minutes | classbeat',
  'meta.example.90s-dance-intervals.description':
    'A made-up 30 minute interval ride with dance-length efforts and six sprints, shown as a chart and a table.',
  'meta.example.endurance-ride.title': 'Endurance ride, 60 minutes | classbeat',
  'meta.example.endurance-ride.description':
    'A made-up easy 60 minute endurance ride, mostly in zone 2, shown as a chart and a table.',
  'meta.example.running-intervals.title': 'Running intervals, 30 minutes | classbeat',
  'meta.example.running-intervals.description':
    'A made-up 30 minute running class with incline intervals and a final push, shown as a chart and a table.',

  // The list of examples.
  'examples.title': 'Example classes',
  'examples.lead': 'Four made-up classes, each with the request it could come from.',
  'examples.note': 'All four are invented. None names a real song.',
  'examples.list': 'The examples',
  'examples.card.meta': '{sport} · {length}',

  // One example.
  'examples.back': 'All examples',
  'examples.length': '{minutes} minutes',
  'examples.sport.cycling': 'Cycling',
  'examples.sport.running': 'Running',
  'examples.facts': 'About this class',
  'examples.fact.sport': 'Sport',
  'examples.fact.length': 'Length',
  'examples.fact.audience': 'For',
  'examples.fact.goals': 'Goals',
  'examples.request.title': 'The request',
  'examples.request.lead': 'What you could write when you describe your class.',
  'examples.class.title': 'The class',
  'examples.class.lead': 'Effort over time, then the same intervals in a table.',
  'examples.table.caption': 'Every interval of {title}, in order',
  'examples.col.part': 'Part',
  'examples.col.start': 'Starts',
  'examples.col.length': 'Length',
  'examples.col.zone': 'Zone',
  'examples.col.setting': 'Setting',
  'examples.col.cue': 'Cue',
  'examples.col.total': 'Total',
  'examples.notes.title': 'Good to know',
  'examples.notes.music': 'This class has no music. Songs are chosen when you design a real class.',
  'examples.notes.english': 'Part names and cues are in English. Classes we design are in English whichever language you use the site in.',
  'examples.notes.running':
    'Running classes use a playlist already in your MOWL music. They are switched on per plan, so they may not be available to you yet.',

  // The words of each example. Numbers here are checked against the class.
  'examples.hill-challenge.title': 'Hill challenge',
  'examples.hill-challenge.summary': 'Three climbs in zones 3 and 4, each longer than the last, then a hard finish.',
  'examples.hill-challenge.audience': 'Riders in an evening class who like long, steady efforts.',
  'examples.hill-challenge.prompt': 'A 45 minute ride with three climbs and a hard finish, for a Thursday evening crowd.',
  'examples.hill-challenge.goal1': 'Build climbing strength.',
  'examples.hill-challenge.goal2': 'Practise holding a steady effort.',
  'examples.hill-challenge.goal3': 'Finish strong after three climbs.',

  'examples.90s-dance-intervals.title': '90s dance ride',
  'examples.90s-dance-intervals.summary': 'Five rounds of 30 seconds hard and 30 seconds easier, then six sprints.',
  'examples.90s-dance-intervals.audience': 'A Friday night party crowd that likes short, punchy efforts.',
  'examples.90s-dance-intervals.prompt': 'A 30 minute 90s party ride with dance intervals and six sprints.',
  'examples.90s-dance-intervals.goal1': 'Keep the room moving.',
  'examples.90s-dance-intervals.goal2': 'Practise quick changes of effort.',
  'examples.90s-dance-intervals.goal3': 'Finish together on a high.',

  'examples.endurance-ride.title': 'Endurance ride',
  'examples.endurance-ride.summary': 'A steady 60 minutes, mostly in zone 2, with three short lifts into zone 3.',
  'examples.endurance-ride.audience': 'Newer riders, or anyone who wants an easy day that still counts.',
  'examples.endurance-ride.prompt': 'A 60 minute easy endurance ride.',
  'examples.endurance-ride.goal1': 'Build an aerobic base.',
  'examples.endurance-ride.goal2': 'Keep the effort even and conversational.',
  'examples.endurance-ride.goal3': 'Recover from a hard week.',

  'examples.running-intervals.title': 'Running intervals',
  'examples.running-intervals.summary': 'Five rounds of two minutes on the incline, then a final push in zone 5.',
  'examples.running-intervals.audience': 'Runners who want structured work on the incline.',
  'examples.running-intervals.prompt': 'A 30 minute running class with five incline intervals and a hard finish.',
  'examples.running-intervals.goal1': 'Practise running steadily uphill.',
  'examples.running-intervals.goal2': 'Build speed with short rests.',
  'examples.running-intervals.goal3': 'Learn to pace a hard finish.',
} as const
