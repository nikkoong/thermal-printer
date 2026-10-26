import { Plugin } from "@opencode/plugin"
import { spawn } from "node:child_process"
import { mkdirSync, openSync, readFileSync } from "node:fs"
import { basename, join } from "node:path"

type PrintInput = {
  message: string
  title?: string
  style?: "note" | "receipt" | "quote" | "sticker" | "task"
  footer?: string
  qr?: string
  image_path?: string
  copies?: number
  dry_run?: boolean
  invert_image?: boolean
  feed_dots?: number
  density?: number
  label_mode?: boolean
}

type StatusInput = Record<string, never>

type PrintResult = {
  ok?: boolean
  dry_run?: boolean
  job?: {
    width?: number
    height?: number
    duration_ms?: number
    transport?: string
    preview_url?: string
  }
  preview_url?: string
  detail?: string
}

const DEFAULT_URL = "http://127.0.0.1:8787"

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export default Plugin.define({
  id: "thermal-printer",

  async setup(ctx) {
    const options = (ctx.options ?? {}) as {
      url?: string
      autoStart?: boolean
      projectDir?: string
      command?: string
      requireApproval?: boolean
    }
    const baseUrl = (options.url || process.env.THERMAL_URL || DEFAULT_URL).replace(/\/+$/, "")
    const autoStart = options.autoStart !== false
    const projectDir =
      options.projectDir ||
      process.env.THERMAL_PROJECT_DIR ||
      ctx.location.project?.canonical ||
      ctx.location.directory

    let starting: Promise<boolean> | null = null

    async function healthy(): Promise<boolean> {
      try {
        const response = await fetch(`${baseUrl}/api/health`, {
          signal: AbortSignal.timeout(900),
        })
        if (!response.ok) return false
        const data = (await response.json()) as { ok?: boolean }
        return data.ok === true
      } catch {
        return false
      }
    }

    async function startDaemon(): Promise<boolean> {
      if (!autoStart) return false
      if (await healthy()) return true
      if (starting) return starting

      starting = (async () => {
        try {
          const port = new URL(baseUrl).port || "8787"
          const host = new URL(baseUrl).hostname || "127.0.0.1"
          const logDir = join(projectDir, ".thermal")
          mkdirSync(logDir, { recursive: true })
          const logPath = join(logDir, "daemon.log")
          const fd = openSync(logPath, "a")
          const args = options.command
            ? options.command.split(/\s+/)
            : [
                "run",
                "--project",
                projectDir,
                "thermal",
                "serve",
                "--host",
                host,
                "--port",
                port,
              ]
          const child = spawn("uv", args, {
            cwd: projectDir,
            detached: true,
            stdio: ["ignore", fd, fd],
            env: { ...process.env, PYTHONUNBUFFERED: "1" },
          })
          child.unref()

          for (let attempt = 0; attempt < 50; attempt += 1) {
            await sleep(300)
            if (await healthy()) return true
          }
          return false
        } catch {
          return false
        } finally {
          starting = null
        }
      })()

      return starting
    }

    async function ensureDaemon(toolContext: { signal?: AbortSignal }): Promise<string | null> {
      if (await healthy()) return null
      if (!autoStart) {
        return `The thermal printer service is not running. Start it with: cd ${projectDir} && uv run thermal serve --open`
      }
      const started = await startDaemon()
      if (started) return null
      return `Could not start the thermal printer service. Run it manually: cd ${projectDir} && uv run thermal serve --open. Check ${join(projectDir, ".thermal", "daemon.log")} for details.`
    }

    await ctx.tool.transform((editor) => {
      editor.namespace({
        name: "thermal",
        description: "Print physical notes on the local tiny thermal printer.",
      })

      editor.add({
        name: "print",
        description:
          "Print a short physical note on the user's mini thermal printer. Use this when the user asks for a printout, wants a physical summary or receipt, or says to print a message. Supports a small markdown subset: headings, bullets, checkboxes, quotes, fenced code, **bold**, *italic*, and `code`. Keep messages short (under ~30 printed lines); never include secrets, API keys, tokens, or private data unless the user explicitly asks. Prefer dry_run first if unsure how it will look. The printer is 384 px wide (about 48 mm).",
        input: {
          type: "object",
          properties: {
            message: {
              type: "string",
              description: "Body of the note. Markdown-lite is supported. Keep it short.",
            },
            title: {
              type: "string",
              description: "Optional heading, e.g. 'BUILD COMPLETE' or 'TODAY'.",
            },
            style: {
              type: "string",
              enum: ["note", "receipt", "quote", "sticker", "task"],
              description:
                "Layout: note (default), receipt (monospace), quote (large serif), sticker (auto-sized bold), task (checkbox list).",
            },
            footer: {
              type: "string",
              description:
                "Optional small footer. An empty string hides the automatic timestamp footer.",
            },
            qr: {
              type: "string",
              description: "Optional URL or text to render as a QR code.",
            },
            image_path: {
              type: "string",
              description: "Optional absolute path to an image to print below the text.",
            },
            copies: {
              type: "integer",
              minimum: 1,
              maximum: 5,
              description: "Number of copies (default 1).",
            },
            dry_run: {
              type: "boolean",
              description: "Render and save a preview without sending anything to the printer.",
            },
            invert_image: {
              type: "boolean",
              description: "Invert the optional image.",
            },
            feed_dots: {
              type: "integer",
              minimum: 0,
              maximum: 255,
              description: "Extra paper feed after printing, in dots.",
            },
            density: {
              type: "integer",
              minimum: 0,
              maximum: 2,
              description: "Print density (0 light, 1 normal, 2 dark).",
            },
            label_mode: {
              type: "boolean",
              description: "Use die-cut label positioning commands.",
            },
          },
          required: ["message"],
          additionalProperties: false,
        },
        execute: async (rawInput, toolContext) => {
          const input = rawInput as PrintInput
          if (options.requireApproval) {
            await ctx.permission.assert({
              sessionID: toolContext.sessionID,
              agent: toolContext.agent,
              source: { type: "tool", messageID: toolContext.messageID, id: toolContext.id },
              action: "thermal.print",
              resources: [baseUrl],
            })
          }

          const startupError = await ensureDaemon(toolContext)
          if (startupError) return { content: startupError }

          let imageBase64: string | undefined
          if (input.image_path) {
            try {
              const bytes = readFileSync(input.image_path)
              if (bytes.byteLength > 12 * 1024 * 1024) {
                return { content: "Image is larger than 12 MB; resize it first." }
              }
              imageBase64 = bytes.toString("base64")
            } catch (error) {
              return { content: `Could not read image_path: ${String(error)}` }
            }
          }

          const body = {
            message: input.message,
            title: input.title ?? null,
            style: input.style ?? "note",
            footer: input.footer ?? null,
            qr: input.qr ?? null,
            image_base64: imageBase64 ?? null,
            invert_image: input.invert_image ?? false,
            copies: input.copies ?? 1,
            dry_run: input.dry_run ?? false,
            feed_dots: input.feed_dots ?? null,
            density: input.density ?? null,
            label_mode: input.label_mode ?? null,
            source: `opencode:${basename(projectDir)}`,
          }

          try {
            const response = await fetch(`${baseUrl}/api/print`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
              signal: toolContext.signal,
            })
            if (!response.ok) {
              let detail = `${response.status} ${response.statusText}`
              try {
                const data = (await response.json()) as { detail?: string }
                if (data.detail) detail = data.detail
              } catch {
                // Keep the HTTP status text.
              }
              return { content: `Thermal print failed: ${detail}` }
            }
            const result = (await response.json()) as PrintResult
            const job = result.job ?? {}
            const preview = result.preview_url || job.preview_url
            const lines = [
              result.dry_run
                ? `Preview saved (${job.width ?? "?"}x${job.height ?? "?"} px); nothing sent to the printer.`
                : `Printed ${job.width ?? "?"}x${job.height ?? "?"} px on ${job.transport ?? "printer"} in ${job.duration_ms ?? 0} ms.`,
            ]
            if (preview) lines.push(`Preview: ${baseUrl}${preview}`)
            return { content: lines.join("\n") }
          } catch (error) {
            return {
              content: `Thermal print failed: ${String(error)}. Is the printer connected? Open ${baseUrl} to check.`,
            }
          }
        },
      })

      editor.add({
        name: "status",
        description:
          "Check whether the local thermal printer is connected, its battery level, paper/cover status, and model. Use before printing if the user asks about printer state.",
        input: {
          type: "object",
          properties: {},
          additionalProperties: false,
        },
        execute: async (rawInput, toolContext) => {
          const startupError = await ensureDaemon(toolContext)
          if (startupError) return { content: startupError }
          try {
            const response = await fetch(`${baseUrl}/api/status?refresh=true`, {
              signal: toolContext.signal,
            })
            if (!response.ok) return { content: `Status failed: ${response.status} ${response.statusText}` }
            const data = (await response.json()) as Record<string, unknown>
            return { content: JSON.stringify(data, null, 2) }
          } catch (error) {
            return { content: `Status failed: ${String(error)}` }
          }
        },
      })
    })
  },
})
