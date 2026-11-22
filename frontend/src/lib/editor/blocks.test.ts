import { describe, expect, it } from 'vitest'
import { decodeBlocks, minSec, totalSec } from './blocks'
import { CONTENT, blocksOf } from './editorFixtures'

describe('decodeBlocks', () => {
  it('numbers blocks flat across segments in class order with their class-time spans', () => {
    const blocks = blocksOf()
    expect(blocks.map((b) => [b.index, b.segment, b.segmentKind, b.startMs, b.endMs])).toEqual([
      [0, 0, 'warmup', 0, 60_000],
      [1, 1, 'main', 60_000, 180_000],
      [2, 1, 'main', 180_000, 270_000],
      [3, 1, 'main', 270_000, 330_000],
      [4, 2, 'cooldown', 330_000, 360_000],
    ])
    expect(totalSec(blocks)).toBe(360)
  })

  it('reads the attributes, the game and the FTP test', () => {
    const [, , ftp, game] = blocksOf()
    expect(ftp).toMatchObject({ ftpTest: true, zone: 5 })
    expect(game).toMatchObject({ game: 'racetrack', ftpTest: false, rpm: [80, 90] })
  })

  it('is null for content it does not edit: a running draft, no segments, a bad second count', () => {
    expect(decodeBlocks(null)).toBeNull()
    expect(decodeBlocks({ segments: [] })).toBeNull()
    expect(decodeBlocks({ segments: [{ name: 'a', kind: 'main', intervals: [{ sec: 60, zone: 2, gradient: 1 }] }] })).toBeNull()
    expect(decodeBlocks({ segments: [{ name: 'a', kind: 'main', intervals: [{ sec: 1.5, zone: 2, rpm: 'beat' }] }] })).toBeNull()
  })

  it('accepts a beat cadence', () => {
    const [block] = decodeBlocks({ segments: [{ name: 'a', kind: 'main', intervals: [{ sec: 60, zone: 2, rpm: 'beat', standing: true }] }] }) ?? []
    expect(block).toMatchObject({ rpm: 'beat', standing: true })
  })

  it('has a 10 s minimum for a game and the block after it, 1 s elsewhere', () => {
    const blocks = blocksOf(CONTENT)
    expect(minSec(blocks, 0)).toBe(1)
    expect(minSec(blocks, 3)).toBe(10)
    expect(minSec(blocks, 4)).toBe(10)
  })
})
