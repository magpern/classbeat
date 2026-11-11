import type { HtmlTagDescriptor, Plugin } from 'vite'

// The two faces the Latin text of every public page is set in: Inter (body) and Archivo (titles) at
// their `latin` subset, which holds sv and da letters too. Without a preload the browser finds the
// font files only after it has parsed the stylesheet and laid the page out, so they arrive after the
// first paint and the swap from the fallback font re-wraps the text under it: a layout shift that
// crosses the takeover gate's limit when the second face lands in a later frame (#935).
const PRELOADED = /(^|\/)(inter-latin-wght-normal|archivo-latin-wdth-normal)-[\w-]+\.woff2$/

/** The `<link rel="preload">` tags for the bundle's Latin font files (none when there are none). */
export function fontPreloadTags(files: Iterable<string>): HtmlTagDescriptor[] {
  return [...files]
    .filter((file) => PRELOADED.test(file))
    .sort()
    .map((file) => ({
      tag: 'link',
      // `crossorigin` is required for fonts, even from the same origin, or the preload is not reused.
      attrs: { rel: 'preload', as: 'font', type: 'font/woff2', crossorigin: '', href: `/${file}` },
      injectTo: 'head' as const,
    }))
}

export function fontPreload(): Plugin {
  return {
    name: 'classbeat-font-preload',
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        return fontPreloadTags(Object.keys(ctx.bundle ?? {}))
      },
    },
  }
}
