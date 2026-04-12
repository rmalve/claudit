import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApi } from '../hooks/useApi'

import {
  SEVERITY_COLORS, AUDITOR_COLORS, FINDING_TYPE_COLORS,
  CONFIDENCE_COLORS,
  getTooltipStyle, getAxisTickStyle, getGridColor,
} from '../utils/chartTheme'
import dayjs from 'dayjs'

import Box from '@mui/material/Box'
import Paper from '@mui/material/Paper'
import Typography from '@mui/material/Typography'
import Autocomplete from '@mui/material/Autocomplete'
import TextField from '@mui/material/TextField'
import Chip from '@mui/material/Chip'
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider'
import { AdapterDayjs } from '@mui/x-date-pickers/AdapterDayjs'
import { DatePicker } from '@mui/x-date-pickers/DatePicker'

import {
  PieChart, Pie, Cell, Legend,
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  BarChart, Bar,
  ResponsiveContainer, Tooltip,
} from 'recharts'

import Card from '../components/Card'

const DIRECTIVE_BAR_COLORS = {
  PENDING: '#C4A95B',
  ACKNOWLEDGED: '#3B82D9',
  VERIFICATION_PENDING: '#8B6AAE',
  VERIFIED_COMPLIANT: '#4D8C00',
  VERIFIED_NON_COMPLIANT: '#D44A4A',
  NON_COMPLIANT: '#D44A4A',
  SUPERSEDED: '#7A7A72',
  ESCALATED: '#D97757',
}

export default function Overview() {
  const navigate = useNavigate()

  const [selectedProjects, setSelectedProjects] = useState([])
  const [startDate, setStartDate] = useState(dayjs().subtract(7, 'day'))
  const [endDate, setEndDate] = useState(dayjs())

  const projectParam = selectedProjects.length === 1 ? selectedProjects[0] : ''
  const projectQuery = projectParam ? `project=${projectParam}&` : ''
  const dateParams = `start_date=${startDate.toISOString()}&end_date=${endDate.toISOString()}`

  const { data: stats, loading } = useApi(`/api/stats?${projectQuery}${dateParams}`, { refreshInterval: 15000 })
  const { data: streams } = useApi('/api/streams', { refreshInterval: 15000 })
  const { data: dayData } = useApi(`/api/findings/by-day?${projectQuery}${dateParams}`, { refreshInterval: 15000 })
  const { data: typeData } = useApi(`/api/findings/by-type?${projectQuery}${dateParams}`, { refreshInterval: 15000 })
  const { data: confData } = useApi(`/api/findings/by-confidence?${projectQuery}${dateParams}`, { refreshInterval: 15000 })
  const { data: dirStatusData } = useApi(`/api/directives/by-status?${projectQuery}`, { refreshInterval: 15000 })

  const allProjects = stats?.active_projects || []
  const sev = stats?.findings_by_severity || {}

  const pieData = useMemo(() =>
    Object.entries(sev).filter(([, v]) => v > 0).map(([name, value]) => ({ name, value })),
    [sev]
  )

  const excludedDayKeys = new Set(['date', 'tool_calls', 'total_findings', 'total_rate'])

  const lineData = useMemo(() => {
    const days = dayData?.days || []
    return days.map((d) => ({
      name: d.date,
      tool_calls: d.tool_calls,
      total_findings: d.total_findings,
      total_rate: d.total_rate,
      ...Object.fromEntries(
        Object.entries(d).filter(([k]) => !excludedDayKeys.has(k))
      ),
    }))
  }, [dayData])

  const auditorKeys = useMemo(() => {
    const keys = new Set()
    ;(dayData?.days || []).forEach(d => {
      Object.keys(d).forEach(k => {
        if (!excludedDayKeys.has(k)) keys.add(k)
      })
    })
    return Array.from(keys)
  }, [dayData])

  const findingTypeBarData = useMemo(() =>
    Object.entries(typeData?.by_type || {}).map(([name, value]) => ({ name, value })),
    [typeData]
  )

  const confidencePieData = useMemo(() =>
    Object.entries(confData?.by_confidence || {})
      .filter(([, v]) => v > 0)
      .map(([name, value]) => ({ name, value })),
    [confData]
  )

  const directiveBarData = useMemo(() => {
    const byTypeStatus = dirStatusData?.by_type_status || {}
    return Object.entries(byTypeStatus).map(([type, statuses]) => ({
      name: type,
      ...statuses,
    }))
  }, [dirStatusData])

  const directiveStatuses = useMemo(() => {
    const statuses = new Set()
    ;(directiveBarData || []).forEach(d => {
      Object.keys(d).forEach(k => { if (k !== 'name') statuses.add(k) })
    })
    return Array.from(statuses)
  }, [directiveBarData])

  const heatmapPlaceholder = useMemo(() => {
    const categories = ['Auth/Security', 'Data Quality', 'Performance', 'Scope Violation', 'Documentation']
    return categories.map(cat => ({
      category: cat,
      findings: Math.floor(Math.random() * 10),
      sessions: Math.floor(Math.random() * 5) + 1,
    }))
  }, [])

  const handleSeverityPieClick = (entry) => {
    if (entry?.name) navigate(`/findings?severity=${entry.name}`)
  }
  const handleTypeBarClick = (data) => {
    if (data?.name) navigate(`/findings?finding_type=${data.name}`)
  }
  const handleConfidencePieClick = (entry) => {
    if (entry?.name) navigate(`/findings?confidence_range=${encodeURIComponent(entry.name)}`)
  }
  const handleDirectiveBarClick = (data, _index, event) => {
    const status = event?.dataKey || ''
    if (status && data?.name) navigate(`/directives?type=${data.name}&status=${status}`)
  }
  const handleHeatmapClick = (data) => {
    if (data?.category) navigate(`/findings?cluster=${encodeURIComponent(data.category)}`)
  }

  const RateTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null
    const data = payload[0]?.payload || {}
    const style = getTooltipStyle()
    return (
      <div style={style}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
        <div style={{ opacity: 0.6, marginBottom: 6 }}>
          {data.total_findings} findings / {data.tool_calls} tool calls
        </div>
        {payload.map((p) => (
          <div key={p.dataKey} style={{ color: p.stroke, display: 'flex', justifyContent: 'space-between', gap: 16 }}>
            <span>{p.dataKey}</span>
            <span style={{ fontWeight: 600 }}>{p.value != null ? p.value.toFixed(2) : '—'}</span>
          </div>
        ))}
        <div style={{ fontWeight: 600, borderTop: `1px solid var(--brand-border)`, marginTop: 6, paddingTop: 4 }}>
          Overall: {data.total_rate != null ? data.total_rate.toFixed(2) : '—'} per 100 calls
        </div>
      </div>
    )
  }

  if (loading) {
    return <Box sx={{ p: 3, color: 'text.secondary' }}>Loading...</Box>
  }

  const tickStyle = getAxisTickStyle()
  const gridColor = getGridColor()

  return (
    <LocalizationProvider dateAdapter={AdapterDayjs}>
      <Box sx={{ p: 3, display: 'flex', flexDirection: 'column', gap: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography variant="h5" color="text.primary">
            Claudit Overview
          </Typography>
        </Box>

        <Paper sx={{ p: 2, display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
          <Autocomplete
            multiple
            size="small"
            options={allProjects}
            value={selectedProjects}
            onChange={(_, val) => setSelectedProjects(val)}
            renderTags={(value, getTagProps) =>
              value.map((option, index) => (
                <Chip size="small" label={option} {...getTagProps({ index })} key={option} />
              ))
            }
            renderInput={(params) => (
              <TextField {...params} label="Projects" placeholder={selectedProjects.length ? '' : 'All'} />
            )}
            sx={{ minWidth: 250 }}
          />
          <DatePicker
            label="Start"
            value={startDate}
            onChange={setStartDate}
            slotProps={{ textField: { size: 'small' } }}
          />
          <DatePicker
            label="End"
            value={endDate}
            onChange={setEndDate}
            slotProps={{ textField: { size: 'small' } }}
          />
          <Typography variant="caption" color="text.disabled" sx={{ ml: 'auto' }}>
            {stats?.total_findings || 0} findings in range
          </Typography>
        </Paper>

        {/* Row 1: Severity pie + Auditor line chart */}
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 3 }}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" color="text.disabled" gutterBottom>
              FINDINGS BY SEVERITY
            </Typography>
            {pieData.length > 0 ? (
              <ResponsiveContainer width="100%" height={280}>
                <PieChart>
                  <Pie
                    data={pieData} cx="50%" cy="50%"
                    innerRadius={50} outerRadius={100}
                    paddingAngle={2} dataKey="value"
                    onClick={handleSeverityPieClick}
                    style={{ cursor: 'pointer' }}
                  >
                    {pieData.map((entry) => (
                      <Cell key={entry.name} fill={SEVERITY_COLORS[entry.name] || '#7A7A72'} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={getTooltipStyle()} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <Box sx={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Typography color="text.disabled">No findings in range</Typography>
              </Box>
            )}
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" color="text.disabled" gutterBottom>
              FINDING RATE BY AUDITOR (per 100 tool calls)
            </Typography>
            {lineData.length > 0 ? (
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={lineData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                  <XAxis dataKey="name" tick={tickStyle} />
                  <YAxis tick={tickStyle} />
                  <Tooltip content={<RateTooltip />} />
                  <Legend />
                  {auditorKeys.map((key) => (
                    <Line
                      key={key} type="monotone" dataKey={key}
                      stroke={AUDITOR_COLORS[key] || '#7A7A72'}
                      strokeWidth={2} dot={{ r: 4 }} connectNulls
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <Box sx={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Typography color="text.disabled">No findings data in range</Typography>
              </Box>
            )}
          </Paper>
        </Box>

        {/* Row 2: Finding type bar + Confidence pie */}
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 3 }}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" color="text.disabled" gutterBottom>
              FINDINGS BY STATUS
            </Typography>
            {findingTypeBarData.length > 0 ? (
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={findingTypeBarData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                  <XAxis dataKey="name" tick={tickStyle} />
                  <YAxis tick={tickStyle} />
                  <Tooltip contentStyle={getTooltipStyle()} />
                  <Bar dataKey="value" onClick={handleTypeBarClick} style={{ cursor: 'pointer' }} radius={[4, 4, 0, 0]}>
                    {findingTypeBarData.map((entry) => (
                      <Cell key={entry.name} fill={FINDING_TYPE_COLORS[entry.name] || '#7A7A72'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <Box sx={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Typography color="text.disabled">No findings in range</Typography>
              </Box>
            )}
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" color="text.disabled" gutterBottom>
              FINDINGS BY CONFIDENCE SCORE
            </Typography>
            {confidencePieData.length > 0 ? (
              <ResponsiveContainer width="100%" height={280}>
                <PieChart>
                  <Pie
                    data={confidencePieData} cx="50%" cy="50%"
                    innerRadius={50} outerRadius={100}
                    paddingAngle={2} dataKey="value"
                    onClick={handleConfidencePieClick}
                    style={{ cursor: 'pointer' }}
                  >
                    {confidencePieData.map((entry) => (
                      <Cell key={entry.name} fill={CONFIDENCE_COLORS[entry.name] || '#7A7A72'} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={getTooltipStyle()} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <Box sx={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Typography color="text.disabled">No findings in range</Typography>
              </Box>
            )}
          </Paper>
        </Box>

        {/* Row 3: Directive status bar + Prevalence heatmap */}
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 3 }}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" color="text.disabled" gutterBottom>
              DIRECTIVES BY TYPE AND STATUS
            </Typography>
            {directiveBarData.length > 0 ? (
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={directiveBarData}>
                  <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                  <XAxis dataKey="name" tick={tickStyle} />
                  <YAxis tick={tickStyle} />
                  <Tooltip contentStyle={getTooltipStyle()} />
                  <Legend />
                  {directiveStatuses.map((status) => (
                    <Bar
                      key={status} dataKey={status} stackId="directives"
                      fill={DIRECTIVE_BAR_COLORS[status] || '#7A7A72'}
                      onClick={(data) => handleDirectiveBarClick(data, 0, { dataKey: status })}
                      style={{ cursor: 'pointer' }}
                      radius={[2, 2, 0, 0]}
                    />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <Box sx={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Typography color="text.disabled">No directives issued</Typography>
              </Box>
            )}
          </Paper>

          <Paper sx={{ p: 2, position: 'relative' }}>
            <Typography variant="subtitle2" color="text.disabled" gutterBottom>
              MOST PREVALENT FINDING CLUSTERS
            </Typography>
            <Box
              sx={{
                position: 'absolute', top: 8, right: 12,
                bgcolor: 'warning.main', color: 'text.primary',
                px: 1, py: 0.25, borderRadius: 1, opacity: 0.8,
              }}
            >
              <Typography variant="caption" fontWeight="bold">PLACEHOLDER</Typography>
            </Box>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={heatmapPlaceholder} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
                <XAxis type="number" tick={tickStyle} />
                <YAxis dataKey="category" type="category" tick={tickStyle} width={120} />
                <Tooltip contentStyle={getTooltipStyle()} />
                <Legend />
                <Bar dataKey="findings" fill="#D44A4A" name="Findings" onClick={handleHeatmapClick} style={{ cursor: 'pointer' }} radius={[0, 4, 4, 0]} />
                <Bar dataKey="sessions" fill="#3B82D9" name="Affected Sessions" onClick={handleHeatmapClick} style={{ cursor: 'pointer' }} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
            <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 1 }}>
              Future: k-nearest neighbor clustering of findings with similar root causes and remediation patterns.
            </Typography>
          </Paper>
        </Box>

        {/* Stream Activity */}
        <Paper sx={{ p: 2 }}>
          <Typography variant="subtitle2" color="text.disabled" gutterBottom>
            STREAM ACTIVITY
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 2 }}>
            {Object.entries(streams?.streams || {}).map(([name, count]) => (
              <Card key={name} title={name.replace('audit:', '')} value={count} />
            ))}
          </Box>
        </Paper>
      </Box>
    </LocalizationProvider>
  )
}
