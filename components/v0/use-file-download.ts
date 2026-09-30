"use client"

import { useCallback, useState } from "react"

export type FileDownloadState = "idle" | "downloading" | "done" | "error"

/**
 * In-browser file download with real progress. XHR rather than fetch: its
 * progress events report loaded/total without a hand-rolled stream reader.
 * The response is saved through a Blob so the browser always downloads with
 * the artifact's filename instead of navigating away, regardless of upstream
 * Content-Disposition.
 */
export function useFileDownload(url: string | null, name: string) {
  const [state, setState] = useState<FileDownloadState>("idle")
  const [progress, setProgress] = useState<number | null>(null)

  const download = useCallback(() => {
    if (!url || state === "downloading") return
    setState("downloading")
    setProgress(null)
    const xhr = new XMLHttpRequest()
    xhr.open("GET", url)
    xhr.responseType = "blob"
    xhr.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        setProgress(event.loaded / event.total)
      }
    }
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        setState("error")
        return
      }
      const href = URL.createObjectURL(xhr.response as Blob)
      const anchor = document.createElement("a")
      anchor.href = href
      anchor.download = name
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(href)
      setState("done")
    }
    xhr.onerror = () => setState("error")
    xhr.onabort = () => setState("idle")
    xhr.send()
  }, [url, name, state])

  return { state, progress, download }
}
