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
      className="app-glass-edge sticky top-0 z-30 flex items-center justify-between gap-2 border-b bg-background/50 px-4 py-4 backdrop-blur-xl sm:gap-4 sm:px-8"
    >
      <div className="flex items-center gap-2.5">
        <span
          aria-hidden="true"
          className="app-glass-edge grid size-7 place-items-center rounded-md border bg-white/[0.04] backdrop-blur-sm transition-shadow duration-500 hover:shadow-[0_0_16px_-2px_oklch(0.62_0.17_250/0.6)]"
        >
          <svg
            className="size-4 text-foreground"
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="0.75"
            aria-hidden="true"
          >
            <path
              d="M14.2 14.2H17V6.9375C17 4.76288 15.2371 3 13.0625 3H5.8V5.8M14.2 14.2V7.79063L7.79062 14.2H14.2ZM14.2 14.2V17H6.9375C4.76288 17 3 15.2371 3 13.0625V5.8H5.8M5.8 5.8V12.2313L12.2313 5.8H5.8Z"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <span className="hidden font-mono text-xs uppercase tracking-[0.25em] text-muted-foreground sm:inline">
          v0 / studio
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
            className="text-muted-foreground hover:text-foreground"
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
          className="shrink text-muted-foreground hover:text-foreground md:hidden"
        />
        <Button
          render={<Link href="/compare">Compare models</Link>}
          nativeButton={false}
          variant="ghost"
          size="sm"
          className="hidden text-muted-foreground hover:text-foreground md:inline-flex"
        />
        <Button
          variant="ghost"
          size="sm"
          className="hidden text-muted-foreground hover:text-foreground md:inline-flex"
        >
          Sign in
        </Button>
        <Button
          size="sm"
          className="shrink-0 rounded-full bg-foreground px-3 text-background transition-transform duration-300 hover:scale-[1.03] hover:bg-foreground/90 active:scale-95 sm:px-4"
        >
          Start building
        </Button>
      </div>
    </motion.header>
  )
}
