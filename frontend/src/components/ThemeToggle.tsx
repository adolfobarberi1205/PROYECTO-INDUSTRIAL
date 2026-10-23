import { useEffect, useState } from 'react'
import { Moon, Sun } from 'lucide-react'

type Theme = 'dark' | 'light'

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.dataset.theme === 'light' ? 'light' : 'dark',
  )

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem('etl_theme', theme)
    } catch {
      // The toggle still works for this visit in restricted browsers.
    }
  }, [theme])

  useEffect(() => {
    function syncPreference(event: StorageEvent) {
      if (event.key === 'etl_theme' || event.key === null) {
        setTheme(event.newValue === 'light' ? 'light' : 'dark')
      }
    }
    window.addEventListener('storage', syncPreference)
    return () => window.removeEventListener('storage', syncPreference)
  }, [])

  return (
    <button
      type="button"
      className="icon-button theme-toggle"
      role="switch"
      aria-checked={theme === 'light'}
      aria-label="Modo claro"
      title={theme === 'dark' ? 'Activar modo claro' : 'Activar modo oscuro'}
      onClick={() => setTheme(current => current === 'dark' ? 'light' : 'dark')}
    >
      {theme === 'dark' ? <Sun size={20} aria-hidden="true" /> : <Moon size={20} aria-hidden="true" />}
    </button>
  )
}
