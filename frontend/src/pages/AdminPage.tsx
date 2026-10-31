import { Link, useRouter } from '../router/Router'
import { AdminLayout } from './admin/AdminLayout'
import { Codes } from './admin/Codes'
import { UserDetail } from './admin/UserDetail'
import { Users } from './admin/Users'

const USER = /^\/admin\/users\/([0-9a-fA-F-]{36})$/

/**
 * The admin area (ADR 0011 section 6), loaded as its own chunk by `App` and only for a session
 * whose role is `admin`; the server's `whoami` decides in `AdminLayout`.
 */
export function AdminPage() {
  const { pathname } = useRouter()
  const path = pathname.replace(/\/+$/, '') || '/'
  const user = USER.exec(path)
  let page
  if (user?.[1] !== undefined) page = <UserDetail key={user[1]} userId={user[1]} />
  else if (path === '/admin' || path === '/admin/users') page = <Users />
  else if (path === '/admin/codes') page = <Codes />
  else {
    page = (
      <p>
        Page not found. <Link to="/admin/users">Back to users</Link>
      </p>
    )
  }
  return <AdminLayout>{page}</AdminLayout>
}

export default AdminPage
