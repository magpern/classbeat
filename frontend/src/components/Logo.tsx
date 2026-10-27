/** classbeat mark: four rounded bars, like beats or an effort profile. Decorative. */
export function Logo() {
  return (
    <svg className="logo" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect className="logo__tile" width="32" height="32" rx="8" />
      <g className="logo__bars">
        <rect x="5.5" y="17" width="3.5" height="8" rx="1.75" />
        <rect x="10.75" y="11" width="3.5" height="14" rx="1.75" />
        <rect x="16" y="6" width="3.5" height="19" rx="1.75" />
        <rect x="21.25" y="13" width="3.5" height="12" rx="1.75" />
      </g>
    </svg>
  )
}
