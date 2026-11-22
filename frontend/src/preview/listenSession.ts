import type { ClipService } from './clipService'
import { ListenTimer } from './listenTimer'

// One listening session of the rendered-clip transport (ADR 0042 section 6.3): the server's session
// id from Play, the counter, and the 1 s tick that only asks whether the counter reached 15:00 (it
// never adds time). Pure of the elements: the media events reach the counter through `timer`.

/** How often the listening limit is checked. */
export const TICK_MS = 1000

export class ListenSession {
  readonly timer: ListenTimer
  private id: string | null = null
  private pending: Promise<string> | null = null
  private tickId: ReturnType<typeof setInterval> | null = null
  private readonly service: ClipService
  private readonly onLimit: () => void

  constructor(service: ClipService, now: () => number, onLimit: () => void) {
    this.service = service
    this.timer = new ListenTimer(now)
    this.onLimit = onLimit
  }

  /** The session id once Play has made one. */
  get current(): string | null {
    return this.id
  }

  /** Play has asked for a session that has not answered yet. */
  get starting(): boolean {
    return this.pending !== null
  }

  /**
   * A session exists afterwards unless `stillWanted` says the request was replaced while the server
   * answered (then the one made stays unused). A second caller shares the request in flight.
   */
  async ensure(stillWanted: () => boolean): Promise<void> {
    this.pending ??= this.service.createSession().finally(() => {
      this.pending = null
    })
    const id = await this.pending
    if (!stillWanted() || this.id !== null) return
    this.id = id
    this.timer.reset()
    this.tickId = setInterval(() => {
      if (this.timer.reached()) this.onLimit()
    }, TICK_MS)
  }

  /** The session is over: no id, no tick, the counter stopped. */
  end(): void {
    this.timer.stop()
    if (this.tickId !== null) clearInterval(this.tickId)
    this.tickId = null
    this.id = null
  }
}
