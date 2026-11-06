/* Sets <html lang> before first paint (ADR 0023 section 2). Only a locale in this list, which equals
   src/i18n/locales.json (a test pins it), is ever used: cb-locale, then navigator.languages, then en. */
(function () {
  var locales = ['en', 'sv', 'da']
  var lang = 'en'
  function pick(tag) {
    var code = String(tag).split(/[-_]/)[0].toLowerCase()
    return locales.indexOf(code) === -1 ? null : code
  }
  try {
    var saved = window.localStorage.getItem('cb-locale')
    if (saved !== null && locales.indexOf(saved) !== -1) lang = saved
    else {
      var languages = window.navigator.languages || []
      for (var i = 0; i < languages.length; i++) {
        var found = pick(languages[i])
        if (found !== null) {
          lang = found
          break
        }
      }
    }
  } catch {
    /* storage or navigator blocked: stay on en */
  }
  document.documentElement.setAttribute('lang', lang)
})()
