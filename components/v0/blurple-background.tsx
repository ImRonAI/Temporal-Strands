/**
 * Editorial blurple ambience.
 * A pair of slow-drifting radial glows sit behind everything, softened by a
 * heavy blur and a fine grid so the surface reads as "paper, lit from behind"
 * rather than a flat gradient. Purely decorative — hidden from assistive tech.
 */
export function BlurpleBackground() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden"
    >
      {/* base wash */}
      <div className="absolute inset-0 bg-background" />

      {/* primary blurple bloom, upper-left */}
      <div className="absolute -left-[12%] -top-[18%] h-[78vh] w-[78vh] animate-blurple-drift rounded-full bg-[radial-gradient(circle_at_center,oklch(0.62_0.23_277/0.85),transparent_66%)] blur-[80px]" />

      {/* secondary cooler bloom, lower-right */}
      <div className="absolute -bottom-[22%] -right-[8%] h-[68vh] w-[68vh] animate-blurple-drift-alt rounded-full bg-[radial-gradient(circle_at_center,oklch(0.66_0.2_300/0.7),transparent_68%)] blur-[90px]" />

      {/* tight accent core glowing behind the headline */}
      <div className="absolute left-1/2 top-[34%] h-[46vh] w-[46vh] -translate-x-1/2 animate-blurple-drift rounded-full bg-[radial-gradient(circle_at_center,oklch(0.58_0.22_285/0.6),transparent_70%)] blur-[110px]" />

      {/* fine editorial grid */}
      <div className="absolute inset-0 opacity-[0.05] [background-image:linear-gradient(to_right,white_1px,transparent_1px),linear-gradient(to_bottom,white_1px,transparent_1px)] [background-size:64px_64px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)]" />

      {/* vignette to ground the type */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_52%,oklch(0.13_0.02_280/0.78))]" />
    </div>
  )
}
