/**
 * Three small bars that beat while something is working (motion-app.css): the logo's motif as a
 * "busy" mark. Decorative: the text beside it says what is happening.
 */
export function BeatBars() {
  return (
    <span className="beat-bars" aria-hidden="true">
      <span />
      <span />
      <span />
    </span>
  )
}
