import { CompareView } from "@/components/v0/compare-view"
import { BlurpleBackground } from "@/components/v0/blurple-background"
import { SiteHeader } from "@/components/v0/site-header"

export default function ComparePage() {
  return (
    <main className="relative flex h-dvh flex-col overflow-hidden">
      <BlurpleBackground settled />
      <SiteHeader />
      <div className="w-full shrink-0 px-4 py-4">
        <h1 className="font-editorial text-3xl text-foreground sm:text-4xl">
          Compare models
        </h1>
        <p className="mt-2 max-w-2xl text-pretty text-sm leading-relaxed text-muted-foreground">
          Compare two to five agents side by side, with the full chat, tools, and previews in every pane.
        </p>
      </div>
      <CompareView />
    </main>
  )
}
