import { describe, expect, it } from 'vitest'
import { LEFT, PLOT_W, VIEW_W, pxToDeltaSec, widthOf, xOf } from './geometry'

describe('geometry', () => {
  it('puts the start at the left edge and the end at the right', () => {
    expect(xOf(360_000, 0)).toBe(LEFT)
    expect(xOf(360_000, 360_000)).toBe(LEFT + PLOT_W)
    expect(xOf(360_000, 180_000)).toBe(LEFT + PLOT_W / 2)
  })

  it('clamps a time outside the class and survives an empty class', () => {
    expect(xOf(360_000, -5)).toBe(LEFT)
    expect(xOf(360_000, 999_999)).toBe(LEFT + PLOT_W)
    expect(xOf(0, 10)).toBe(LEFT)
    expect(widthOf(0, 10)).toBe(0)
    expect(widthOf(100, 25)).toBe(PLOT_W / 4)
  })

  it('turns a pixel drag into whole seconds of class time, at any drawing width', () => {
    // a drawing 1:1 with its units: the full plot is the full class
    expect(pxToDeltaSec(PLOT_W, VIEW_W, 360)).toBe(360)
    expect(pxToDeltaSec(PLOT_W / 2, VIEW_W, 360)).toBe(180)
    // the drawing shown at half size: twice the pixels' worth of units
    expect(pxToDeltaSec(PLOT_W / 4, VIEW_W / 2, 360)).toBe(180)
    expect(pxToDeltaSec(-PLOT_W / 4, VIEW_W, 360)).toBe(-90)
  })

  it('moves nothing for a drawing without width', () => {
    expect(pxToDeltaSec(100, 0, 360)).toBe(0)
    expect(pxToDeltaSec(100, VIEW_W, 0)).toBe(0)
  })
})
