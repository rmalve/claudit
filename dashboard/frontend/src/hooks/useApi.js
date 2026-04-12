import { useState, useEffect, useCallback } from 'react'

export function useApi(url, options = {}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const { refreshInterval, enabled = true } = options

  const fetchData = useCallback(async () => {
    if (!enabled) return
    try {
      const res = await fetch(url)
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const json = await res.json()
      setData(json)
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [url, enabled])

  useEffect(() => {
    fetchData()
    if (refreshInterval) {
      const id = setInterval(fetchData, refreshInterval)
      return () => clearInterval(id)
    }
  }, [fetchData, refreshInterval])

  return { data, loading, error, refetch: fetchData }
}
