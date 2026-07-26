import { CompareView } from "@/components/v0/compare-view"
import { BlurpleBackground } from "@/components/v0/blurple-background"
import { SiteHeader } from "@/components/v0/site-header"

export default function ComparePage() {
  return (
    <main className="relative flex min-h-screen flex-col">
      <BlurpleBackground />
      <SiteHeader />
      <div className="mx-auto w-full max-w-6xl px-4 pt-4">
        <h1 className="font-editorial text-3xl text-foreground sm:text-4xl">
          Compare models
        </h1>
        <p className="mt-2 max-w-2xl text-pretty text-sm leading-relaxed text-muted-foreground">
          Send one prompt to up to four models from the Agent API side by side.
        </p>
      </div>
      <CompareView />
    </main>
  )
}
