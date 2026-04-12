import { useApi } from '../hooks/useApi'
import { formatTime } from '../utils/time'
import Box from '@mui/material/Box'
import Paper from '@mui/material/Paper'
import Typography from '@mui/material/Typography'
import LinearProgress from '@mui/material/LinearProgress'
import Card from '../components/Card'

function TaskPipelineBar({ auditor, data }) {
  const { assigned, completed, failed, pending } = data
  const progress = assigned > 0 ? ((completed + failed) / assigned) * 100 : 100
  const hasFailures = failed > 0
  const isDone = pending === 0 && assigned > 0

  return (
    <div className="bg-brand-surface border border-brand-border rounded-lg p-3">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-sm font-medium text-brand-text capitalize">{auditor}</span>
        <div className="flex items-center gap-2">
          {isDone && <span className="text-brand-green text-xs">Done</span>}
          {hasFailures && <span className="text-brand-red text-xs">{failed} failed</span>}
          <span className="text-xs text-brand-text-tertiary">
            {completed + failed}/{assigned}
          </span>
        </div>
      </div>
      <LinearProgress
        variant="determinate"
        value={progress}
        sx={{
          height: 6,
          borderRadius: 3,
          backgroundColor: 'var(--brand-bg-secondary)',
          '& .MuiLinearProgress-bar': {
            borderRadius: 3,
            backgroundColor: hasFailures ? '#D44A4A' : isDone ? '#4D8C00' : '#D97757',
          },
        }}
      />
      <div className="flex justify-between mt-1 text-[10px] text-brand-text-tertiary">
        <span>{pending} pending</span>
        <span>{completed} completed</span>
      </div>
    </div>
  )
}

export default function SystemHealth() {
  const { data: health } = useApi('/api/health', { refreshInterval: 10000 })
  const { data: collections } = useApi('/api/collections', { refreshInterval: 30000 })
  const { data: pipeline } = useApi('/api/task-pipeline', { refreshInterval: 10000 })

  const pipelineData = pipeline?.pipeline || {}
  const totalAssigned = Object.values(pipelineData).reduce((s, d) => s + d.assigned, 0)
  const totalPending = Object.values(pipelineData).reduce((s, d) => s + d.pending, 0)

  return (
    <Box sx={{ p: 3, display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Typography variant="h5" color="text.primary">
        System Health
      </Typography>

      <Paper sx={{ p: 2 }}>
        <Typography variant="subtitle2" color="text.disabled" gutterBottom>
          INFRASTRUCTURE
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 2 }}>
          <Card
            title="QDrant"
            value={health?.qdrant ? 'Online' : 'Offline'}
            className={health?.qdrant ? 'border-brand-green/30' : 'border-brand-red/30'}
          />
          <Card
            title="Redis"
            value={health?.redis ? 'Online' : 'Offline'}
            className={health?.redis ? 'border-brand-green/30' : 'border-brand-red/30'}
          />
          <Card
            title="Last Check"
            value={formatTime(health?.timestamp)}
          />
        </Box>
      </Paper>

      <Paper sx={{ p: 2 }}>
        <div className="flex items-center justify-between mb-2">
          <Typography variant="subtitle2" color="text.disabled">
            AUDIT TASK PIPELINE
          </Typography>
          <span className="text-xs text-brand-text-tertiary">
            {totalAssigned > 0
              ? `${totalPending} pending of ${totalAssigned} total`
              : 'No tasks assigned'}
          </span>
        </div>
        {Object.keys(pipelineData).length > 0 ? (
          <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 2 }}>
            {Object.entries(pipelineData).map(([auditor, data]) => (
              <TaskPipelineBar key={auditor} auditor={auditor} data={data} />
            ))}
          </Box>
        ) : (
          <div className="text-brand-text-tertiary text-sm text-center py-4">
            No audit tasks in the pipeline.
          </div>
        )}
      </Paper>

      <Paper sx={{ p: 2 }}>
        <Typography variant="subtitle2" color="text.disabled" gutterBottom>
          QDRANT COLLECTIONS
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 2 }}>
          {Object.entries(collections?.collections || {}).map(([name, count]) => (
            <Card key={name} title={name} value={count} subtitle="points" />
          ))}
        </Box>
      </Paper>
    </Box>
  )
}
