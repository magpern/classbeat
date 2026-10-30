/* Applies the saved theme before first paint (ADR 0013 section 2). Dark is the default; storage may throw. */
(function () {
  var theme = 'dark'
  try {
    var saved = window.localStorage.getItem('cb-theme')
    if (saved === 'light' || saved === 'system' || saved === 'dark') theme = saved
  } catch {
    /* storage blocked: stay on dark */
  }
  document.documentElement.setAttribute('data-theme', theme)
})()
