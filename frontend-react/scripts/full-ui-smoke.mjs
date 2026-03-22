import { spawn } from 'node:child_process'
import path from 'node:path'

const scripts = [
  ['map_profile', 'map-profile-smoke.mjs'],
  ['schedule', 'schedule-multi-smoke.mjs'],
  ['ranked', 'ranked-multi-smoke.mjs'],
]

function runScript(label, scriptName) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.join('scripts', scriptName)], {
      cwd: process.cwd(),
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    })

    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString()
    })
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString()
    })

    child.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(`${label} smoke failed:\n${stderr || stdout}`))
        return
      }

      try {
        resolve(JSON.parse(stdout))
      } catch (error) {
        reject(new Error(`${label} smoke returned invalid JSON:\n${stdout}\n${String(error)}`))
      }
    })
  })
}

async function run() {
  const results = {}

  for (const [label, scriptName] of scripts) {
    results[label] = await runScript(label, scriptName)
  }

  console.log(JSON.stringify(results, null, 2))
}

run().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
