import { useState, useEffect, useRef } from 'react'

const BUILDING_TYPES = [
  { value: '', label: 'Any type' },
  { value: 'house', label: 'House' },
  { value: 'apartment', label: 'Apartment' },
  { value: 'villa', label: 'Villa' },
  { value: 'townhouse', label: 'Townhouse' },
  { value: 'studio', label: 'Studio' },
]

const PRICE_OPTIONS = [
  50000, 75000, 100000, 125000, 150000, 175000, 200000, 225000, 250000,
  275000, 300000, 350000, 400000, 450000, 500000, 600000, 750000, 1000000,
]

const SOURCE_COLORS = {
  Immoweb: 'bg-blue-100 text-blue-800',
  Zimmo: 'bg-green-100 text-green-800',
  Immoscoop: 'bg-purple-100 text-purple-800',
  ERA: 'bg-orange-100 text-orange-800',
  Realo: 'bg-pink-100 text-pink-800',
  'Logic Immo': 'bg-yellow-100 text-yellow-800',
  Heylen: 'bg-teal-100 text-teal-800',
  Jamar: 'bg-indigo-100 text-indigo-800',
}

const SOURCE_GROUPS = [
  {
    label: 'Aggregators',
    sources: [
      { id: 'Immoweb', label: 'Immoweb', available: true },
      { id: 'Zimmo', label: 'Zimmo', available: true },
      { id: 'ERA', label: 'ERA', available: true },
      { id: 'Realo', label: 'Realo', available: true },
      { id: 'Immoscoop', label: 'Immoscoop', available: false },
      { id: 'Logic Immo', label: 'Logic Immo', available: false },
    ],
  },
  {
    label: 'Large agencies',
    sources: [
      { id: 'Heylen', label: 'Heylen Vastgoed', available: true },
      { id: 'Hillewaere', label: 'Hillewaere', available: false },
      { id: 'ImmoPoint', label: 'Immo Point', available: false },
      { id: 'VBVastgoed', label: 'VB Vastgoed', available: false },
      { id: 'ImmoDeLaet', label: 'Immo De Laet', available: false },
      { id: 'Dewaele', label: 'Dewaele', available: false },
      { id: 'Trevi', label: 'Trevi', available: false },
    ],
  },
  {
    label: 'Medium agencies',
    sources: [
      { id: 'Jamar', label: 'Jamar Immo', available: true },
      { id: 'Coprimmo', label: 'Coprimmo', available: false },
      { id: 'Walls', label: 'Walls Vastgoed', available: false },
      { id: 'Immodome', label: 'Immodome', available: false },
      { id: 'Copandi', label: 'Copandi', available: false },
      { id: 'Sorenco', label: 'Sorenco', available: false },
      { id: 'EngelVolkers', label: 'Engel & Völkers', available: false },
    ],
  },
  {
    label: 'Small agencies',
    sources: [
      { id: 'Immassur', label: 'Immassur', available: false },
      { id: 'BRVastgoed', label: 'BR Vastgoed', available: false },
      { id: 'ClissenImmo', label: 'Clissen Immo', available: false },
      { id: 'Sinjoor', label: 'Sinjoor Makelaars', available: false },
      { id: 'Abricasa', label: 'Abricasa', available: false },
      { id: 'Provas', label: 'Provas', available: false },
      { id: 'BoltImmo', label: 'Bolt Immo', available: false },
      { id: 'HansImmo', label: 'Hans Immo', available: false },
      { id: 'JACQ', label: 'JACQ', available: false },
      { id: 'MarkgraveVastgoed', label: 'Markgrave Vastgoed', available: false },
      { id: 'ImmoCS', label: 'Immo C&S', available: false },
      { id: 'Listings', label: 'Listings.be', available: false },
      { id: 'Immovasta', label: 'Immovasta', available: false },
      { id: 'AccentVastgoed', label: 'Accent Vastgoed', available: false },
      { id: 'EST8', label: 'EST8 Vastgoed', available: false },
    ],
  },
]

const ALL_AVAILABLE = SOURCE_GROUPS.flatMap(g => g.sources.filter(s => s.available).map(s => s.id))
const DEFAULT_SOURCES = ['Heylen']

// ── Helpers ────────────────────────────────────────────────────────────────

function formatDate(dateStr) {
  if (!dateStr) return null
  const listed = new Date(dateStr)
  const now = new Date()
  const diffMs = now - listed
  const diffDays = Math.floor(diffMs / 86400000)
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  if (diffDays < 7) return `${diffDays} days ago`
  return listed.toLocaleDateString('en-BE', { day: 'numeric', month: 'short', year: 'numeric' })
}

function DateLabel({ listedDate, firstSeen }) {
  if (listedDate) {
    return (
      <span className="text-xs text-gray-400 shrink-0" title="Date posted on source">
        {formatDate(listedDate)}
      </span>
    )
  }
  if (firstSeen) {
    return (
      <span className="text-xs text-gray-300 italic shrink-0" title="Date fetched by us (post date unknown)">
        fetched {formatDate(firstSeen.slice(0, 10))}
      </span>
    )
  }
  return null
}

async function fetchInterests() {
  try {
    const res = await fetch('/api/interests')
    if (res.ok) return await res.json()
  } catch {}
  return {}
}

async function saveInterest(link, status) {
  await fetch('/api/interests', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ link, status }),
  })
}

// ── Sub-components ──────────────────────────────────────────────────────────

function SourceBadge({ source }) {
  const cls = SOURCE_COLORS[source] || 'bg-gray-100 text-gray-700'
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded-full shrink-0 ${cls}`}>
      {source}
    </span>
  )
}

function PropertyCard({ property, viewMode, interest, onInterest }) {
  const isGrid = viewMode === 'grid'

  return (
    <div className={`relative group bg-white rounded-xl border overflow-hidden transition-all duration-200 ${
      interest === 'interesting'
        ? 'border-green-300 shadow-sm shadow-green-100'
        : interest === 'not_interesting'
        ? 'border-red-200 opacity-60'
        : 'border-gray-200 hover:border-blue-400 hover:shadow-md'
    } ${isGrid ? 'flex flex-col' : 'flex flex-row'}`}>

      {/* Interest buttons */}
      <div className="absolute top-2 right-2 z-10 flex gap-1">
        <button
          type="button"
          title="Interesting"
          onClick={() => onInterest(property.link, interest === 'interesting' ? null : 'interesting')}
          className={`w-7 h-7 rounded-full flex items-center justify-center text-sm shadow transition-colors ${
            interest === 'interesting'
              ? 'bg-green-500 text-white'
              : 'bg-white/90 text-gray-400 hover:bg-green-50 hover:text-green-500'
          }`}
        >★</button>
        <button
          type="button"
          title="Not interesting"
          onClick={() => onInterest(property.link, interest === 'not_interesting' ? null : 'not_interesting')}
          className={`w-7 h-7 rounded-full flex items-center justify-center text-sm shadow transition-colors ${
            interest === 'not_interesting'
              ? 'bg-red-400 text-white'
              : 'bg-white/90 text-gray-400 hover:bg-red-50 hover:text-red-400'
          }`}
        >✕</button>
      </div>

      <a
        href={property.link}
        target="_blank"
        rel="noopener noreferrer"
        className={`flex ${isGrid ? 'flex-col flex-1' : 'flex-row flex-1'} min-w-0`}
      >
        <div className={`bg-gray-100 overflow-hidden shrink-0 ${isGrid ? 'h-44 w-full' : 'h-28 w-44'}`}>
          {property.image_url ? (
            <img
              src={property.image_url}
              alt={property.title}
              className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
              onError={e => { e.target.style.display = 'none' }}
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-gray-300">
              <svg className="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
              </svg>
            </div>
          )}
        </div>
        <div className="p-3.5 flex flex-col flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2 mb-1">
            <span className="text-lg font-bold text-gray-900 leading-tight">
              {property.price_text || 'Price on request'}
            </span>
            <SourceBadge source={property.source} />
          </div>
          <p className="text-sm text-gray-700 font-medium truncate mb-0.5">{property.title}</p>
          {(() => {
            const mapsAddr = property.street
              ? `${property.street}, ${property.location || property.postcode}, Belgium`
              : (property.location || property.postcode)
                ? `${property.location || property.postcode}, Belgium`
                : null
            const displayAddr = property.street
              ? `${property.street}, ${property.location || property.postcode}`
              : property.location || property.postcode
            return mapsAddr ? (
              <a
                href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(mapsAddr)}`}
                target="_blank"
                rel="noopener noreferrer"
                onClick={e => e.stopPropagation()}
                className="text-xs text-gray-500 mb-2 hover:text-blue-600 hover:underline block truncate"
              >
                📍 {displayAddr}
              </a>
            ) : (
              <p className="text-xs text-gray-500 mb-2">📍 —</p>
            )
          })()}
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-600 mb-2">
            <span>🛏 {property.bedrooms != null ? property.bedrooms : <span className="text-gray-300">—</span>}</span>
            <span>📐 {property.sqm ? `${property.sqm} m²` : <span className="text-gray-300">—</span>}</span>
            <span>
              🌿 {property.garden_sqm
                ? `${property.garden_sqm} m²`
                : property.garden === true
                ? 'Yes'
                : property.garden === false
                ? 'No'
                : <span className="text-gray-300">—</span>}
            </span>
          </div>
          <div className="flex items-center justify-end mt-auto">
            <DateLabel listedDate={property.listed_date} firstSeen={property.first_seen} />
          </div>
        </div>
      </a>
    </div>
  )
}

function PostcodeInput({ postcodes, onChange }) {
  const [input, setInput] = useState('')

  const add = (val) => {
    const code = val.trim()
    if (/^\d{4}$/.test(code) && !postcodes.includes(code)) onChange([...postcodes, code])
    setInput('')
  }
  const remove = (code) => onChange(postcodes.filter(p => p !== code))
  const handleKey = (e) => {
    if (e.key === 'Enter' || e.key === ',' || e.key === ' ') { e.preventDefault(); if (input) add(input) }
    else if (e.key === 'Backspace' && !input && postcodes.length > 0) remove(postcodes[postcodes.length - 1])
  }

  return (
    <div
      className="flex flex-wrap gap-1.5 p-2 border border-gray-300 rounded-lg min-h-[42px] focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-blue-500 bg-white cursor-text"
      onClick={() => document.getElementById('postcode-input').focus()}
    >
      {postcodes.map(pc => (
        <span key={pc} className="flex items-center gap-1 bg-blue-100 text-blue-800 text-xs font-medium px-2 py-0.5 rounded-full">
          {pc}
          <button type="button" onClick={e => { e.stopPropagation(); remove(pc) }} className="text-blue-500 hover:text-blue-900 ml-0.5">×</button>
        </span>
      ))}
      <input
        id="postcode-input"
        type="text"
        value={input}
        onChange={e => setInput(e.target.value.replace(/\D/g, '').slice(0, 4))}
        onKeyDown={handleKey}
        onBlur={() => input && add(input)}
        placeholder={postcodes.length === 0 ? 'e.g. 2000, 9000, 1000…' : ''}
        className="outline-none text-sm flex-1 min-w-[80px] bg-transparent"
      />
    </div>
  )
}

function SourcesPanel({ enabled, onChange }) {
  const [open, setOpen] = useState(false)

  const toggle = (id) => {
    onChange(enabled.includes(id) ? enabled.filter(s => s !== id) : [...enabled, id])
  }

  const toggleGroup = (group) => {
    const available = group.sources.filter(s => s.available).map(s => s.id)
    const allChecked = available.every(id => enabled.includes(id))
    onChange(allChecked ? enabled.filter(id => !available.includes(id)) : [...enabled, ...available.filter(id => !enabled.includes(id))])
  }

  return (
    <div className="mt-3 border border-gray-200 rounded-xl overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 bg-gray-50 hover:bg-gray-100 transition-colors text-sm"
      >
        <span className="font-medium text-gray-700 flex items-center gap-2">
          Sources
          <span className="text-xs font-normal text-gray-400">{enabled.length}/{ALL_AVAILABLE.length} active</span>
        </span>
        <svg className={`w-4 h-4 text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="p-4 bg-white space-y-4">
          {SOURCE_GROUPS.map(group => {
            const available = group.sources.filter(s => s.available)
            const allChecked = available.length > 0 && available.every(s => enabled.includes(s.id))
            const someChecked = available.some(s => enabled.includes(s.id))

            return (
              <div key={group.label}>
                <div className="flex items-center gap-2 mb-2">
                  {available.length > 0 ? (
                    <button type="button" onClick={() => toggleGroup(group)}
                      className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors ${
                        allChecked ? 'bg-blue-600 border-blue-600' : someChecked ? 'bg-blue-200 border-blue-400' : 'border-gray-300 bg-white'
                      }`}>
                      {(allChecked || someChecked) && (
                        <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d={allChecked ? "M5 13l4 4L19 7" : "M5 12h14"} />
                        </svg>
                      )}
                    </button>
                  ) : <div className="w-4 h-4 shrink-0" />}
                  <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{group.label}</span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1.5 pl-6">
                  {group.sources.map(source => {
                    const checked = enabled.includes(source.id)
                    return (
                      <label key={source.id} className={`flex items-center gap-2 text-xs rounded-lg px-2.5 py-1.5 border transition-colors select-none ${
                        source.available
                          ? checked ? 'border-blue-300 bg-blue-50 text-gray-800 cursor-pointer hover:bg-blue-100'
                                    : 'border-gray-200 bg-white text-gray-700 cursor-pointer hover:bg-gray-50'
                          : 'border-dashed border-gray-200 bg-gray-50 text-gray-400 cursor-not-allowed'
                      }`}>
                        <input type="checkbox" className="sr-only" checked={checked} disabled={!source.available}
                          onChange={() => source.available && toggle(source.id)} />
                        <span className={`w-3.5 h-3.5 rounded border shrink-0 flex items-center justify-center ${
                          source.available ? (checked ? 'bg-blue-600 border-blue-600' : 'border-gray-300 bg-white') : 'border-gray-200 bg-gray-100'
                        }`}>
                          {checked && source.available && (
                            <svg className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                            </svg>
                          )}
                        </span>
                        <span className="truncate">{source.label}</span>
                        {!source.available && <span className="ml-auto text-gray-300 shrink-0">soon</span>}
                      </label>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Main app ────────────────────────────────────────────────────────────────

export default function App() {
  const [criteria, setCriteria] = useState({
    transaction: 'buy',
    building_type: 'house',
    price_min: '300000',
    price_max: '450000',
    garden: 'yes',
    sqm_min: '100',
    sqm_max: '',
    bedrooms_min: '2',
    postcodes: ['2000', '2018', '2020', '2050', '2060', '2100', '2140', '2600'],
  })
  const [enabledSources, setEnabledSources] = useState(DEFAULT_SOURCES)
  const [results, setResults] = useState([])
  const [sources, setSources] = useState({})
  const [errors, setErrors] = useState([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [newListingsCount, setNewListingsCount] = useState(0)
  const [pendingCriteria, setPendingCriteria] = useState(null)
  const pollRef = useRef(null)
  const [viewMode, setViewMode] = useState('grid')
  const [sortBy, setSortBy] = useState('date_desc')
  const [interestFilter, setInterestFilter] = useState('all')
  const [sourceFilter, setSourceFilter] = useState([])
  const [interests, setInterests] = useState({})

  useEffect(() => { fetchInterests().then(setInterests) }, [])

  const startPolling = (scrapeId, criteria) => {
    if (pollRef.current) clearInterval(pollRef.current)
    setRefreshing(true)
    setNewListingsCount(0)
    setPendingCriteria(criteria)
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/scrape-status/${scrapeId}`)
        if (!res.ok) return
        const { done, new_count } = await res.json()
        if (done) {
          clearInterval(pollRef.current)
          pollRef.current = null
          setRefreshing(false)
          if (new_count > 0) setNewListingsCount(new_count)
        }
      } catch {}
    }, 3000)
  }

  const applyNewListings = async () => {
    if (!pendingCriteria) return
    setNewListingsCount(0)
    setLoading(true)
    try {
      const res = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pendingCriteria),
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const data = await res.json()
      setResults(data.results || [])
      setSources(data.sources || {})
      setSourceFilter([])
      setErrors(data.errors || [])
      if (data.scrape_id) startPolling(data.scrape_id, pendingCriteria)
    } catch (err) {
      setErrors([err.message])
    } finally {
      setLoading(false)
    }
  }

  const set = (key, val) => setCriteria(prev => ({ ...prev, [key]: val }))

  const markInterest = (link, value) => {
    setInterests(prev => {
      const next = { ...prev }
      if (value === null) delete next[link]
      else next[link] = value
      return next
    })
    saveInterest(link, value)
  }

  const handleSearch = async (e) => {
    e.preventDefault()
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    setNewListingsCount(0)
    setRefreshing(false)
    setLoading(true)
    setErrors([])

    const payload = {
      transaction: criteria.transaction,
      building_type: criteria.building_type || null,
      price_min: criteria.price_min ? parseInt(criteria.price_min) : null,
      price_max: criteria.price_max ? parseInt(criteria.price_max) : null,
      garden: criteria.garden === 'any' ? null : criteria.garden,
      sqm_min: criteria.sqm_min ? parseInt(criteria.sqm_min) : null,
      sqm_max: criteria.sqm_max ? parseInt(criteria.sqm_max) : null,
      bedrooms_min: criteria.bedrooms_min ? parseInt(criteria.bedrooms_min) : null,
      postcodes: criteria.postcodes,
      enabled_sources: enabledSources,
    }

    try {
      const res = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const data = await res.json()
      setResults(data.results || [])
      setSources(data.sources || {})
      setSourceFilter([])
      setErrors(data.errors || [])
      if (data.scrape_id) startPolling(data.scrape_id, payload)
    } catch (err) {
      setErrors([err.message])
      setResults([])
      setSources({})
    } finally {
      setLoading(false)
      setSearched(true)
    }
  }

  // Sort
  const sortKey = (p) => p.listed_date || (p.first_seen ? p.first_seen.slice(0, 10) : null)

  const sorted = [...results].sort((a, b) => {
    if (sortBy === 'price_asc') return (a.price ?? Infinity) - (b.price ?? Infinity)
    if (sortBy === 'price_desc') return (b.price ?? -Infinity) - (a.price ?? -Infinity)
    if (sortBy === 'date_desc') {
      const da = sortKey(a) || '0000-00-00'
      const db = sortKey(b) || '0000-00-00'
      return db.localeCompare(da)
    }
    if (sortBy === 'date_asc') {
      const da = sortKey(a) || '9999-99-99'
      const db = sortKey(b) || '9999-99-99'
      return da.localeCompare(db)
    }
    return 0
  })

  // Interest + source filter
  const filtered = sorted.filter(p => {
    const i = interests[p.link]
    if (interestFilter === 'interesting') { if (i !== 'interesting') return false }
    else if (interestFilter === 'not_interesting') { if (i !== 'not_interesting') return false }
    else { if (i === 'not_interesting') return false }
    if (sourceFilter.length > 0 && !sourceFilter.includes(p.source)) return false
    return true
  })

  const interestCounts = {
    all: results.filter(p => interests[p.link] !== 'not_interesting').length,
    interesting: results.filter(p => interests[p.link] === 'interesting').length,
    not_interesting: results.filter(p => interests[p.link] === 'not_interesting').length,
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center gap-3">
          <div className="w-9 h-9 bg-blue-600 rounded-lg flex items-center justify-center shrink-0">
            <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
            </svg>
          </div>
          <div>
            <h1 className="text-base font-bold text-gray-900 leading-tight">House Finder Belgium</h1>
            <p className="text-xs text-gray-400">Aggregates listings across Belgian real estate sites</p>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-6">
        {/* Search form */}
        <form onSubmit={handleSearch} className="bg-white rounded-2xl border border-gray-200 p-5 mb-6 shadow-sm">
          <div className="mb-3">
            <div className="flex rounded-lg border border-gray-300 overflow-hidden text-sm w-fit">
              <button type="button" onClick={() => set('transaction', 'buy')}
                className={`px-5 py-2 transition-colors ${criteria.transaction === 'buy' ? 'bg-blue-600 text-white font-semibold' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
                To buy
              </button>
              <button type="button" onClick={() => set('transaction', 'rent')}
                className={`px-5 py-2 border-l border-gray-300 transition-colors ${criteria.transaction === 'rent' ? 'bg-blue-600 text-white font-semibold' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
                To rent
              </button>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Property type</label>
              <select value={criteria.building_type} onChange={e => set('building_type', e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                {BUILDING_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Min price</label>
              <select value={criteria.price_min} onChange={e => set('price_min', e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                <option value="">No min</option>
                {PRICE_OPTIONS.map(p => <option key={p} value={p}>€{p.toLocaleString('nl-BE')}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Max price</label>
              <select value={criteria.price_max} onChange={e => set('price_max', e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                <option value="">No max</option>
                {PRICE_OPTIONS.map(p => <option key={p} value={p}>€{p.toLocaleString('nl-BE')}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Min bedrooms</label>
              <select value={criteria.bedrooms_min} onChange={e => set('bedrooms_min', e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                <option value="">Any</option>
                {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}+</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Min surface (m²)</label>
              <input type="number" value={criteria.sqm_min} onChange={e => set('sqm_min', e.target.value)}
                placeholder="e.g. 80" min="0"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Max surface (m²)</label>
              <input type="number" value={criteria.sqm_max} onChange={e => set('sqm_max', e.target.value)}
                placeholder="e.g. 300" min="0"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent" />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Garden</label>
              <select value={criteria.garden} onChange={e => set('garden', e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent">
                <option value="any">Any</option>
                <option value="yes">Required</option>
                <option value="no">Not needed</option>
              </select>
            </div>
          </div>

          <div className="mt-3">
            <label className="block text-xs font-medium text-gray-600 mb-1">Belgian postcodes — press Enter or Space after each</label>
            <PostcodeInput postcodes={criteria.postcodes} onChange={pcs => set('postcodes', pcs)} />
          </div>

          <SourcesPanel enabled={enabledSources} onChange={setEnabledSources} />

          <div className="mt-4 flex justify-end">
            <button type="submit" disabled={loading || enabledSources.length === 0}
              className="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white font-semibold px-7 py-2 rounded-lg text-sm transition-colors flex items-center gap-2">
              {loading ? (
                <>
                  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Searching…
                </>
              ) : 'Search properties'}
            </button>
          </div>
        </form>

        {/* New listings banner */}
        {newListingsCount > 0 && (
          <div className="mb-4 flex items-center justify-between gap-3 px-4 py-3 bg-blue-600 text-white rounded-xl shadow-md">
            <span className="text-sm font-medium">
              {newListingsCount} new listing{newListingsCount !== 1 ? 's' : ''} found
            </span>
            <button
              type="button"
              onClick={applyNewListings}
              className="text-xs font-semibold bg-white text-blue-600 px-3 py-1 rounded-lg hover:bg-blue-50 transition-colors"
            >
              Show new listings
            </button>
          </div>
        )}

        {errors.length > 0 && (
          <div className="mb-4 p-3.5 bg-amber-50 border border-amber-200 rounded-xl text-sm text-amber-800">
            <p className="font-medium mb-1">Some sources had issues (results from others still shown):</p>
            <ul className="list-disc list-inside space-y-0.5 text-xs">
              {errors.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          </div>
        )}

        {/* Results toolbar */}
        {searched && !loading && (
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <div className="flex items-center gap-2 flex-wrap">
              {/* Interest filter tabs */}
              <div className="flex rounded-lg border border-gray-300 overflow-hidden text-xs">
                {[
                  { key: 'all', label: `All (${interestCounts.all})` },
                  { key: 'interesting', label: `★ Saved (${interestCounts.interesting})` },
                  { key: 'not_interesting', label: `✕ Hidden (${interestCounts.not_interesting})` },
                ].map(tab => (
                  <button key={tab.key} type="button" onClick={() => setInterestFilter(tab.key)}
                    className={`px-3 py-1.5 transition-colors border-l border-gray-300 first:border-l-0 ${
                      interestFilter === tab.key ? 'bg-blue-600 text-white font-semibold' : 'bg-white text-gray-600 hover:bg-gray-50'
                    }`}>
                    {tab.label}
                  </button>
                ))}
              </div>
              {refreshing && (
                <span className="text-xs bg-blue-50 text-blue-500 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                  </svg>
                  checking for new listings
                </span>
              )}
              {Object.keys(sources).length > 0 && Object.entries(sources).map(([src, count]) => {
                const active = sourceFilter.length === 0 || sourceFilter.includes(src)
                const colorCls = SOURCE_COLORS[src] || 'bg-gray-100 text-gray-700'
                return (
                  <button
                    key={src}
                    type="button"
                    onClick={() => {
                      if (sourceFilter.length === 0) {
                        // switch to filtering all others out except this one
                        setSourceFilter(Object.keys(sources).filter(s => s !== src))
                      } else if (sourceFilter.includes(src)) {
                        // remove from hidden list (show it)
                        const next = sourceFilter.filter(s => s !== src)
                        setSourceFilter(next)
                      } else {
                        // add to hidden list
                        const next = [...sourceFilter, src]
                        // if all are now hidden, reset to show all
                        setSourceFilter(next.length === Object.keys(sources).length ? [] : next)
                      }
                    }}
                    className={`text-xs font-medium px-2 py-0.5 rounded-full transition-opacity ${colorCls} ${active ? 'opacity-100' : 'opacity-30'}`}
                  >
                    {src} ({count})
                  </button>
                )
              })}
            </div>
            <div className="flex items-center gap-2">
              <select value={sortBy} onChange={e => setSortBy(e.target.value)}
                className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                <option value="date_desc">Newest first</option>
                <option value="date_asc">Oldest first</option>
                <option value="price_asc">Price: low to high</option>
                <option value="price_desc">Price: high to low</option>
              </select>
              <div className="flex rounded-lg border border-gray-300 overflow-hidden text-sm">
                <button type="button" onClick={() => setViewMode('grid')}
                  className={`px-3 py-1.5 transition-colors ${viewMode === 'grid' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
                  Grid
                </button>
                <button type="button" onClick={() => setViewMode('list')}
                  className={`px-3 py-1.5 border-l border-gray-300 transition-colors ${viewMode === 'list' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
                  List
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Loading skeletons */}
        {loading && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="bg-white rounded-xl border border-gray-200 overflow-hidden animate-pulse">
                <div className="h-44 bg-gray-200" />
                <div className="p-4 space-y-2.5">
                  <div className="h-5 bg-gray-200 rounded w-2/5" />
                  <div className="h-3.5 bg-gray-200 rounded w-3/4" />
                  <div className="h-3 bg-gray-200 rounded w-1/2" />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Results */}
        {!loading && filtered.length > 0 && (
          <div className={viewMode === 'grid' ? 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4' : 'flex flex-col gap-3'}>
            {filtered.map((p, i) => (
              <PropertyCard
                key={p.link || i}
                property={p}
                viewMode={viewMode}
                interest={interests[p.link]}
                onInterest={markInterest}
              />
            ))}
          </div>
        )}

        {!loading && searched && filtered.length === 0 && results.length > 0 && (
          <div className="text-center py-16 text-gray-400">
            <p className="font-medium text-gray-500">No listings match the active filters</p>
            <p className="text-sm mt-1">
              {sourceFilter.length > 0 ? 'Try enabling more sources above, or ' : 'Use '}
              the ★ and ✕ buttons on cards to organise your results
            </p>
          </div>
        )}

        {!loading && searched && results.length === 0 && errors.length === 0 && (
          <div className="text-center py-16 text-gray-400">
            <svg className="w-14 h-14 mx-auto mb-3 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
            </svg>
            <p className="font-medium text-gray-500">No properties found</p>
            <p className="text-sm mt-1">Try relaxing your filters or adding more postcodes</p>
          </div>
        )}

        {!searched && !loading && (
          <div className="text-center py-16 text-gray-400">
            <svg className="w-14 h-14 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <p className="font-medium text-gray-500">Set your criteria and hit Search</p>
            <p className="text-sm mt-1">We'll aggregate listings from Belgian real estate sites simultaneously</p>
          </div>
        )}
      </main>
    </div>
  )
}
