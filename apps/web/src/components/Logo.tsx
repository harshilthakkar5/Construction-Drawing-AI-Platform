import type { CSSProperties } from "react";
import markColor from "@/assets/brand/mark-color.png";
import markGrey from "@/assets/brand/mark-grey.png";
import wordmarkName from "@/assets/brand/wordmark-name.png";
import wordmarkTagline from "@/assets/brand/wordmark-tagline.png";
import { cn } from "@/lib/utils";

/** A single-colour brand image painted in the current text colour, so it follows the theme. */
function Masked({ src, ratio, label, className }: { src: string; ratio: number; label?: string; className?: string }) {
  const mask = `url(${src}) center / contain no-repeat`;
  const style: CSSProperties = { mask, WebkitMask: mask, aspectRatio: ratio };
  return (
    <span
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cn("block shrink-0 bg-current", className)}
      style={style}
    />
  );
}

/**
 * The ArcAligned "AA" mark. The two blue strokes are the client's colours; the
 * ring and the grey leg take the theme's text colour, so the mark reads on light
 * and dark backgrounds alike.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <span aria-hidden className={cn("text-foreground/75 relative block size-9 shrink-0", className)}>
      <Masked src={markGrey} ratio={1} className="absolute inset-0 size-full" />
      <img src={markColor} alt="" className="absolute inset-0 size-full" draggable={false} />
    </span>
  );
}

/** The ArcAligned wordmark with its tagline. Sized in em: set a font size to scale it. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("flex min-w-0 flex-col gap-1", className)}>
      <Masked src={wordmarkName} ratio={289 / 62} label="ArcAligned" className="text-primary h-[1.75em]" />
      <Masked src={wordmarkTagline} ratio={287 / 26} label="services that align success" className="text-muted-foreground h-[0.66em]" />
    </span>
  );
}

/** The login screen's brand: the name alone. */
export function Logo() {
  return <Wordmark className="text-xl" />;
}
