import { cn } from "@/lib/utils";
import type { Experimental_GeneratedImage } from "ai";
import NextImage from "next/image";

export type ImageProps = Experimental_GeneratedImage & {
  className?: string;
  alt?: string;
};

export const Image = ({
  base64,
  mediaType,
  alt,
  className,
}: ImageProps) => (
  <NextImage
    alt={alt ?? "Generated image"}
    className={cn(
      "h-auto max-w-full overflow-hidden rounded-md",
      className
    )}
    height={1024}
    src={`data:${mediaType};base64,${base64}`}
    unoptimized
    width={1024}
  />
);
