import { createContext, useContext, useMemo, useState, useEffect } from 'react'
import { createTheme, ThemeProvider } from '@mui/material/styles'
import CssBaseline from '@mui/material/CssBaseline'

const ThemeModeContext = createContext({ mode: 'light', toggleMode: () => {} })

export function useThemeMode() {
  return useContext(ThemeModeContext)
}

const shared = {
  typography: {
    fontFamily: "'DM Sans', system-ui, -apple-system, sans-serif",
    h5: { fontWeight: 700, letterSpacing: '-0.01em' },
    h6: { fontWeight: 600 },
    subtitle2: {
      fontWeight: 600,
      fontSize: '0.7rem',
      letterSpacing: '0.06em',
      textTransform: 'uppercase',
    },
    body2: { fontSize: '0.875rem' },
  },
  shape: { borderRadius: 8 },
}

function buildTheme(mode) {
  const isLight = mode === 'light'

  return createTheme({
    ...shared,
    palette: {
      mode,
      background: {
        default: isLight ? '#FAF9F5' : '#1F1F1E',
        paper: isLight ? '#FFFFFF' : '#262625',
      },
      primary: { main: '#D97757', dark: '#C6613F', light: '#E8A48D' },
      secondary: { main: '#3B82D9' },
      error: { main: '#D44A4A' },
      success: { main: '#4D8C00' },
      warning: { main: '#C4A95B' },
      text: {
        primary: isLight ? '#141413' : '#FAF9F5',
        secondary: isLight ? '#3D3D3A' : '#C0BFB4',
        disabled: isLight ? '#5E5D59' : '#949389',
      },
      divider: isLight ? 'rgba(20,20,19,0.10)' : 'rgba(224,223,216,0.10)',
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            backgroundColor: isLight ? '#FAF9F5' : '#1F1F1E',
          },
        },
      },
      MuiPaper: {
        defaultProps: { elevation: 0 },
        styleOverrides: {
          root: {
            backgroundImage: 'none',
            border: `1px solid ${isLight ? 'rgba(20,20,19,0.10)' : 'rgba(224,223,216,0.10)'}`,
            borderRadius: 12,
          },
        },
      },
      MuiButton: {
        styleOverrides: {
          root: {
            textTransform: 'none',
            fontWeight: 500,
            borderRadius: 8,
          },
        },
      },
      MuiLinearProgress: {
        styleOverrides: {
          root: {
            borderRadius: 4,
            backgroundColor: isLight ? 'rgba(20,20,19,0.06)' : 'rgba(224,223,216,0.08)',
          },
          bar: {
            borderRadius: 4,
          },
        },
      },
      MuiAutocomplete: {
        styleOverrides: {
          paper: {
            border: `1px solid ${isLight ? 'rgba(20,20,19,0.10)' : 'rgba(224,223,216,0.10)'}`,
            boxShadow: isLight
              ? '0 4px 16px hsl(0 0% 0% / 8%)'
              : '0 4px 16px hsl(0 0% 0% / 32%)',
          },
        },
      },
      MuiTextField: {
        styleOverrides: {
          root: {
            '& .MuiOutlinedInput-root': {
              '& fieldset': {
                borderColor: isLight ? 'rgba(20,20,19,0.15)' : 'rgba(224,223,216,0.15)',
              },
              '&:hover fieldset': {
                borderColor: '#D97757',
              },
              '&.Mui-focused fieldset': {
                borderColor: '#D97757',
              },
            },
          },
        },
      },
      MuiChip: {
        styleOverrides: {
          root: { borderRadius: 9999 },
        },
      },
    },
  })
}

export default function MuiTheme({ children }) {
  const [mode, setMode] = useState(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('theme-mode') || 'light'
    }
    return 'light'
  })

  useEffect(() => {
    const root = document.documentElement
    if (mode === 'dark') {
      root.classList.add('dark')
    } else {
      root.classList.remove('dark')
    }
    localStorage.setItem('theme-mode', mode)
  }, [mode])

  const toggleMode = () => setMode(m => m === 'light' ? 'dark' : 'light')
  const theme = useMemo(() => buildTheme(mode), [mode])

  return (
    <ThemeModeContext.Provider value={{ mode, toggleMode }}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </ThemeModeContext.Provider>
  )
}
