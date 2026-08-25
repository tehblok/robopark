import { Link } from 'react-router-dom'
import { useAuth } from '../../auth-context'
import { ru } from '../../i18n/ru'

export function CookieStaleStub() {
  const { user } = useAuth()
  const canFixCookie = user?.role === 'admin' || user?.role === 'royal'

  return (
    <div className="inspection-stub">
      <div>
        <h2>{ru.emergency.cookieStubTitle}</h2>
        <p>{ru.emergency.cookieStubBody}</p>
        {canFixCookie && (
          <p>
            <Link to="/admin">{ru.emergency.cookieStubAdmin}</Link>
          </p>
        )}
      </div>
    </div>
  )
}
