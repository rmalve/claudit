import { useState, useEffect, useCallback, useRef } from 'react'

/**
 * Reusable infinite scroll hook with cursor-based pagination.
 *
 * Usage:
 *   const { items, loading, hasMore, sentinelRef } = useInfiniteScroll(
 *     '/api/sessions',
 *     { limit: 20, params: { project: 'my-project' }, itemsKey: 'sessions' }
 *   )
 *
 *   return (
 *     <div>
 *       {items.map(item => <Card key={item.id} {...item} />)}
 *       <div ref={sentinelRef} />
 *     </div>
 *   )
 *
 * Options:
 *   limit       - items per page (default 20)
 *   params      - extra query params appended to every request
 *   itemsKey    - key in response JSON that holds the items array (default 'items')
 *   enabled     - set false to disable fetching (default true)
 */
export function useInfiniteScroll(baseUrl, options = {}) {
  const { limit = 20, params = {}, itemsKey = 'items', enabled = true } = options

  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [error, setError] = useState(null)
  const offsetRef = useRef(0)
  const observerRef = useRef(null)
  const sentinelRef = useRef(null)
  const fetchingRef = useRef(false)

  // Build URL with offset, limit, and extra params
  const buildUrl = useCallback((offset) => {
    const qs = new URLSearchParams({ offset: String(offset), limit: String(limit) })
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== '') qs.set(k, String(v))
    }
    return `${baseUrl}?${qs}`
  }, [baseUrl, limit, JSON.stringify(params)])

  // Fetch a single page
  const fetchPage = useCallback(async () => {
    if (fetchingRef.current || !hasMore || !enabled) return
    fetchingRef.current = true
    setLoading(true)

    const requestedOffset = offsetRef.current

    try {
      const url = buildUrl(requestedOffset)
      const res = await fetch(url)
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const json = await res.json()
      const newItems = json[itemsKey] || []

      // Guard against double-fetch: only append if offset hasn't moved past us
      if (offsetRef.current === requestedOffset) {
        setItems(prev => [...prev, ...newItems])
        offsetRef.current = requestedOffset + newItems.length
      }

      if (newItems.length < limit) {
        setHasMore(false)
      }
      setError(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
      fetchingRef.current = false
    }
  }, [buildUrl, hasMore, enabled, itemsKey, limit])

  // Reset when URL or params change
  useEffect(() => {
    setItems([])
    offsetRef.current = 0
    setHasMore(true)
    setError(null)
    fetchingRef.current = false
  }, [baseUrl, JSON.stringify(params), enabled])

  // Fetch first page on mount/reset
  useEffect(() => {
    if (enabled && items.length === 0 && hasMore) {
      fetchPage()
    }
  }, [enabled, items.length, hasMore, fetchPage])

  // IntersectionObserver on sentinel element
  useEffect(() => {
    if (observerRef.current) {
      observerRef.current.disconnect()
    }

    if (!sentinelRef.current || !hasMore || !enabled) return

    observerRef.current = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !fetchingRef.current) {
          fetchPage()
        }
      },
      { rootMargin: '200px' }
    )

    observerRef.current.observe(sentinelRef.current)

    return () => {
      if (observerRef.current) observerRef.current.disconnect()
    }
  }, [hasMore, enabled, fetchPage])

  // Manual reset function (e.g. when filters change)
  const reset = useCallback(() => {
    setItems([])
    offsetRef.current = 0
    setHasMore(true)
    setError(null)
    fetchingRef.current = false
  }, [])

  return { items, loading, hasMore, error, sentinelRef, reset }
}
