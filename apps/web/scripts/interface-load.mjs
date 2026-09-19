import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

// Deliberately in-process: temporary SQLite and stubbed Tracker, no target URL,
// credentials or production network. Both presentations use these same APIs.
if (process.argv.length > 2) throw new Error('This isolated harness accepts no host or credentials')
const root = fileURLToPath(new URL('../../api/', import.meta.url))
for (const mode of ['classic', 'task-first']) {
  console.log(JSON.stringify({ mode, sessions: 200, concurrency: 16, network: 'in-process; no Wi-Fi emulation', presentation: 'not rendered; shared backend contract' }))
  const result = spawnSync(`${root}.venv/bin/python`, ['-m', 'pytest', '-q', '-s', 'tests/test_task_workflow_load.py'], {
    cwd: root, stdio: 'inherit', env: { PATH: process.env.PATH, PYTHONDONTWRITEBYTECODE: '1' },
  })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}
