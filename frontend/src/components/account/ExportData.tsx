import { useState } from 'react'
import { exportMyData } from '../../api/auth'
import { useGuard } from '../../auth/prompts'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'

/** The browser saves `data` as a file; nothing is kept or sent anywhere else. */
function saveAsFile(name: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/**
 * Download my data (ADR 0014 section 2): `GET /api/account/export` through the guard (recent
 * authentication, and a step-up with two-factor on), saved as `classbeat-export-<date>.json`.
 */
export function ExportData() {
  const guard = useGuard()
  const action = useAction()
  const [done, setDone] = useState(false)

  const download = () => {
    setDone(false)
    void action
      .run(async () => {
        const data = await guard(exportMyData)
        saveAsFile(`classbeat-export-${new Date().toISOString().slice(0, 10)}.json`, data)
        return true
      })
      .then((saved) => {
        if (saved === true) setDone(true)
      })
  }

  return (
    <div className="flex flex-col gap-2">
      <p className="text-muted">
        One file with your account, classes and revisions, song lists, jobs, usage and sign-in
        history. Your connections show only their status: never a token, password or key.
      </p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {done ? <Notice kind="success">Your data was saved to a file.</Notice> : null}
      <div>
        <Button variant="secondary" disabled={action.busy} onClick={download}>
          Download my data
        </Button>
      </div>
    </div>
  )
}
