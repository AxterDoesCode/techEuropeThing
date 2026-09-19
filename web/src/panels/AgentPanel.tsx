import type { AgentStatus } from '../types'

export function AgentPanel({ agents }: { agents: AgentStatus[] }) {
  if (agents.length === 0) return null
  return (
    <section className="panel agents">
      <h2>Agents</h2>
      <table>
        <thead>
          <tr><th>Source</th><th>Status</th><th>Runs/h</th><th>New/h</th></tr>
        </thead>
        <tbody>
          {agents.map((a) => (
            <tr key={a.id} className={a.last_status?.startsWith('error') ? 'error' : ''}>
              <td>{a.id}</td>
              <td title={a.last_status ?? ''}>{!a.enabled ? 'disabled' : a.last_status?.startsWith('error') ? 'error' : (a.last_status ?? 'pending')}</td>
              <td>{a.runs_last_hour}</td>
              <td>{a.inserted_last_hour}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
