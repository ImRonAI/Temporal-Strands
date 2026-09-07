import { cn } from "@/lib/utils"

/**
 * Editorial blue-glass ambience.
 * A pair of slow-drifting radial glows sit behind everything, softened by a
 * heavy blur and a fine grid so the surface reads as "paper, lit from behind"
 * rather than a flat gradient. Purely decorative — hidden from assistive tech.
 *
 * `settled` dims the blooms once a conversation exists: the hero stage hands
 * focus to the work, and the glow recedes to ambience.
 */
export function BlurpleBackground({ settled = false }: { settled?: boolean }) {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden"
    >
      {/* base wash */}
      <div className="absolute inset-0 bg-background" />

      {/* blooms recede once the conversation owns the stage */}
      <div
        className={cn(
          "absolute inset-0 transition-opacity duration-[2000ms] ease-out",
          settled ? "opacity-40" : "opacity-100"
        )}
      >
        {/* primary blue bloom, upper-left */}
        <div className="ambient-bloom absolute -left-[12%] -top-[18%] h-[78vh] w-[78vh] animate-blurple-drift rounded-full" />

        {/* complementary blue/purple bloom, lower-right */}
        <div className="ambient-bloom-secondary absolute -bottom-[22%] -right-[8%] h-[68vh] w-[68vh] animate-blurple-drift-alt rounded-full" />

        {/* tight accent core glowing behind the headline */}
        <div className="ambient-bloom-core absolute left-1/2 top-[34%] h-[46vh] w-[46vh] -translate-x-1/2 animate-blurple-drift rounded-full" />
      </div>

      {/* fine editorial grid */}
      <div className="absolute inset-0 opacity-[0.05] [background-image:linear-gradient(to_right,white_1px,transparent_1px),linear-gradient(to_bottom,white_1px,transparent_1px)] [background-size:64px_64px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)]" />

      {/* vignette to ground the type */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_52%,oklch(0.13_0.02_250/0.78))]" />
    </div>
  )
}
