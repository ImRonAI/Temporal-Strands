import { AgentChat } from "@/components/v0/agent-chat"
import { SiteHeader } from "@/components/v0/site-header"

export default function Page() {
  return (
    <main className="relative flex h-dvh flex-col overflow-hidden">
      <SiteHeader />
      <AgentChat />
    </main>
  )
}
