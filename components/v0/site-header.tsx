"use client"

import Link from "next/link"

import { Button } from "@/components/ui/button"

const NAV = ["Community", "Pricing", "Enterprise", "Docs"]

export function SiteHeader() {
  return (
    <header className="relative z-20 flex items-center justify-between gap-4 px-5 py-5 sm:px-8">
      <div className="flex items-center gap-2.5">
        <span
          aria-hidden="true"
          className="grid size-7 place-items-center rounded-md border border-white/10 bg-white/[0.04] backdrop-blur-sm"
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
        <span className="font-mono text-xs uppercase tracking-[0.25em] text-muted-foreground">
          v0 / studio
        </span>
      </div>

      <nav className="hidden items-center gap-1 md:flex">
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

      <div className="flex items-center gap-2">
        <Button
          render={<Link href="/compare">Compare models</Link>}
          nativeButton={false}
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-foreground"
        />
        <Button
          variant="ghost"
          size="sm"
          className="hidden text-muted-foreground hover:text-foreground sm:inline-flex"
        >
          Sign in
        </Button>
        <Button
          size="sm"
          className="rounded-full bg-foreground px-4 text-background hover:bg-foreground/90"
        >
          Start building
        </Button>
      </div>
    </header>
  )
}
