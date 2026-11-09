import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('index.html viewport', () => {
  it('extends under the notch, because the CSS reads the safe-area insets', () => {
    const html = readFileSync(join(import.meta.dirname, '../index.html'), 'utf8')
    expect(html).toMatch(/name="viewport"[^>]*viewport-fit=cover/)
    const tokens = readFileSync(join(import.meta.dirname, 'styles/tokens.css'), 'utf8')
    expect(tokens).toMatch(/env\(safe-area-inset-bottom/)
  })
})
