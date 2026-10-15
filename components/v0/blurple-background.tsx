import { cn } from "@/lib/utils"

/**
 * Gwen daylight ambience.
 * A white canvas with soft pastel blooms — coral, sky, sunshine, lilac, mint —
 * drifting far behind a frosted white haze, so the surface reads as a bright,
 * optimistic morning window rather than a gradient. Purely decorative —
 * hidden from assistive tech.
 *
 * `settled` softens the blooms once a conversation exists: the hero hands
 * focus to the work, and the light recedes to a gentle glow.
 */
export function BlurpleBackground({ settled = false }: { settled?: boolean }) {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden"
    >
      {/* white base */}
      <div className="absolute inset-0 bg-background" />

      {/* pastel light field */}
      <div
        className={cn(
          "absolute inset-0 transition-opacity duration-[2000ms] ease-out",
          settled ? "opacity-55" : "opacity-100"
        )}
      >
        {/* warm coral/peach sun, upper-left */}
        <div className="ambient-bloom absolute -left-[10%] -top-[22%] h-[75vh] w-[75vh] animate-gwen-drift rounded-full" />

        {/* clear sky, upper-right */}
        <div className="ambient-bloom-secondary absolute -right-[12%] -top-[10%] h-[70vh] w-[70vh] animate-gwen-drift-alt rounded-full" />

        {/* sunshine core behind the headline */}
        <div className="ambient-bloom-core absolute left-1/2 top-[30%] h-[50vh] w-[60vh] -translate-x-1/2 animate-gwen-drift rounded-full" />

        {/* lilac, lower-left */}
        <div className="ambient-bloom-lilac absolute -bottom-[25%] left-[8%] h-[60vh] w-[60vh] animate-gwen-drift-alt rounded-full" />

        {/* mint, lower-right */}
        <div className="ambient-bloom-mint absolute -bottom-[20%] -right-[6%] h-[55vh] w-[55vh] animate-gwen-drift rounded-full" />
      </div>

      {/* frosted white haze: the secondary blur that keeps everything bright */}
      <div className="absolute inset-0 bg-white/30" />

      {/* soft white bloom from the centre so content always sits on light */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_55%_45%_at_50%_48%,oklch(1_0_0/0.7),transparent_80%)]" />

      {/* delicate dot grid, faded at the edges */}
      <div className="absolute inset-0 opacity-[0.35] [background-image:radial-gradient(oklch(0.62_0.05_40/0.22)_1px,transparent_1px)] [background-size:28px_28px] [mask-image:radial-gradient(ellipse_at_center,black_20%,transparent_70%)]" />

      {/* paper grain */}
      <div className="ambient-grain absolute inset-0 opacity-[0.05] mix-blend-multiply" />
    </div>
  )
}
