// /health deliberately returns HTTP 200 while degraded. Check its contract,
// plus the container healthcheck, before opening the app for real work.
import { execFile } from "node:child_process"
import { promisify } from "node:util"
import { setTimeout as delay } from "node:timers/promises"

const exec = promisify(execFile)
const timeout = Number(process.env.DEV_READY_TIMEOUT_MS || 180_000)
if (!Number.isFinite(timeout) || timeout <= 0) {
  throw new Error("DEV_READY_TIMEOUT_MS must be a positive number")
}
const deadline = Date.now() + timeout
const context = process.env.DESKTOP_DOCKER_CONTEXT || "colima"
let lastStatus = "API not reachable"
let previousStatus

while (Date.now() < deadline) {
  try {
    const response = await fetch("http://127.0.0.1:8787/health", {
      signal: AbortSignal.timeout(Math.min(12_000, Math.max(1, deadline - Date.now()))),
    })
    if (!response.ok) throw new Error(`API returned HTTP ${response.status}`)
    const health = await response.json()
    const missing = ["temporal", "worker", "pollers", "desktop"].filter(
      (key) => health[key] !== true,
    )
    if (health.status !== "ok") missing.push("healthy API")
    if (!Array.isArray(health.models) || !health.models.length) missing.push("model catalog")
    const { stdout } = await exec("docker", [
      "--context", context, "inspect", "--format", "{{.State.Health.Status}}", "gwen-desktop",
    ], { timeout: Math.min(5_000, Math.max(1, deadline - Date.now())) })
    if (stdout.trim() !== "healthy") missing.push(`desktop container (${stdout.trim()})`)
    if (!missing.length) {
      console.log(`[startup] Backend, ${health.models.length} models, and Linux desktop ready. Starting http://localhost:3001`)
      process.exit(0)
    }
    lastStatus = `Waiting for ${missing.join(", ")}`
  } catch (error) {
    // Do not dump provider responses or environment configuration into logs.
    lastStatus = `Waiting for API/desktop (${error.code || error.name})`
  }
  if (lastStatus !== previousStatus) {
    console.log(`[startup] ${lastStatus}`)
    previousStatus = lastStatus
  }
  await delay(Math.min(1_000, Math.max(1, deadline - Date.now())))
}

console.error(`[startup] Readiness timed out after ${timeout / 1_000}s: ${lastStatus}. Check the worker/API/desktop logs above.`)
process.exit(1)
