"use client";

import { cn } from "@/lib/utils";
import { motion } from "motion/react";
import type { CSSProperties, ElementType, HTMLAttributes } from "react";
import { forwardRef, memo, useMemo } from "react";

const ShimmerElement = forwardRef<
  HTMLElement,
  HTMLAttributes<HTMLElement> & { as: ElementType }
>(({ as: Component, ...props }, ref) => <Component ref={ref} {...props} />);
ShimmerElement.displayName = "ShimmerElement";

// Motion requires generated components to be created outside React render.
const MotionShimmerElement = motion.create(ShimmerElement);

export interface TextShimmerProps {
  children: string;
  as?: ElementType;
  className?: string;
  duration?: number;
  spread?: number;
}

const ShimmerComponent = ({
  children,
  as: Component = "p",
  className,
  duration = 2,
  spread = 2,
}: TextShimmerProps) => {
  const dynamicSpread = useMemo(
    () => (children?.length ?? 0) * spread,
    [children, spread]
  );

  return (
    <MotionShimmerElement
      as={Component}
      animate={{ backgroundPosition: "0% center" }}
      className={cn(
        "relative inline-block bg-[length:250%_100%,auto] bg-clip-text text-transparent",
        "[--bg:linear-gradient(90deg,#0000_calc(50%-var(--spread)),var(--color-background),#0000_calc(50%+var(--spread)))] [background-repeat:no-repeat,padding-box]",
        className
      )}
      initial={{ backgroundPosition: "100% center" }}
      style={
        {
          "--spread": `${dynamicSpread}px`,
          backgroundImage:
            "var(--bg), linear-gradient(var(--color-muted-foreground), var(--color-muted-foreground))",
        } as CSSProperties
      }
      transition={{
        duration,
        ease: "linear",
        repeat: Number.POSITIVE_INFINITY,
      }}
    >
      {children}
    </MotionShimmerElement>
  );
};

export const Shimmer = memo(ShimmerComponent);
