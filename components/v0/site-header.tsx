"use client"

import Link from "next/link"
import { motion } from "motion/react"

import { Button } from "@/components/ui/button"

const NAV = ["Community", "Pricing", "Enterprise", "Docs"]

export function SiteHeader() {
  // No scroll listener: the app shell is h-dvh with overflow hidden, so
  // window.scrollY never changes — inner panes own their scrolling. The
  // header wears its glass treatment statically instead of waiting for a
  // scroll event that can never fire.
  return (
    <motion.header
      initial={{ opacity: 0, y: -12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
      className="sticky top-0 z-30 flex items-center justify-between gap-2 border-b border-white/70 bg-white/55 px-4 py-3 shadow-[0_1px_0_0_oklch(0.915_0.012_55/0.6)] backdrop-blur-2xl backdrop-saturate-150 sm:gap-4 sm:px-8"
    >
      <div className="group flex items-center gap-2.5">
        <span
          aria-hidden="true"
          className="relative grid size-8 place-items-center overflow-hidden rounded-[0.7rem] bg-sunrise shadow-glow ring-1 ring-white/60 transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] group-hover:-rotate-6 group-hover:scale-105"
        >
          <span className="absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,oklch(1_0_0/0.5),transparent_60%)]" />
          <svg
            className="relative size-5 text-white drop-shadow-[0_1px_1px_oklch(0.5_0.15_30/0.35)]"
            viewBox="0 0 24 24"
            fill="currentColor"
            aria-hidden="true"
          >
            <path d="M11.5 3.5c.55 3.9 2.25 5.85 6.25 6.6-4 .72-5.7 2.68-6.25 6.58-.55-3.9-2.25-5.86-6.25-6.58 4-.75 5.7-2.7 6.25-6.6Z" />
            <circle cx="18" cy="17.5" r="1.9" opacity="0.92" />
            
          </svg>
        </span>
        <span className="font-editorial text-[1.35rem] leading-none tracking-[-0.03em] text-foreground">
          Gwen
        </span>
        <span className="hidden rounded-full border border-coral/25 bg-coral/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-blurple-bright sm:inline">
          Beta
        </span>
      </div>

      {/* Secondary nav yields below lg: at exactly 768px the four links plus
          both CTAs overflowed the viewport (scrollWidth 783). */}
      <nav className="hidden items-center gap-1 lg:flex">
        {NAV.map((item) => (
          <Button
            key={item}
            variant="ghost"
            size="sm"
            className="rounded-full font-medium text-muted-foreground hover:bg-coral/8 hover:text-foreground"
          >
            {item}
          </Button>
        ))}
      </nav>

      <div className="flex min-w-0 items-center gap-1 sm:gap-2">
        <Button
          render={<Link href="/compare">Compare</Link>}
          nativeButton={false}
          variant="ghost"
          size="sm"
          className="shrink rounded-full font-medium text-muted-foreground hover:bg-coral/8 hover:text-foreground md:hidden"
        />
        <Button
          render={<Link href="/compare">Compare models</Link>}
          nativeButton={false}
          variant="ghost"
          size="sm"
          className="hidden rounded-full font-medium text-muted-foreground hover:bg-coral/8 hover:text-foreground md:inline-flex"
        />
        <Button
          variant="ghost"
          size="sm"
          className="hidden rounded-full font-medium text-muted-foreground hover:bg-coral/8 hover:text-foreground md:inline-flex"
        >
          Sign in
        </Button>
        <Button
          size="sm"
          className="shrink-0 rounded-full bg-sunrise px-3.5 font-semibold text-primary-foreground shadow-glow ring-1 ring-white/40 transition-[transform,box-shadow] duration-300 hover:scale-[1.03] hover:shadow-[0_14px_34px_-10px_oklch(0.645_0.2_33/0.6)] active:scale-95 sm:px-4.5"
        >
          Get started
        </Button>
      </div>
    </motion.header>
  )
}
