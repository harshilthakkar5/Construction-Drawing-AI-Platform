import tower from "@/assets/brand/auth-tower.jpg";

/**
 * The right half of the auth screens: a still photo of a scaffolded tower and its cranes going
 * up, toned into the palette's blueprint colour, under one headline.
 * Photo: Unsplash, photo-1599707254554-027aeb4deacd (Unsplash License: free for
 * commercial use, no attribution required), stored greyscale so the theme tints it.
 */
export function AuthArt() {
  return (
    <div className="bg-blueprint relative h-full w-full overflow-hidden">
      <img src={tower} alt="" draggable={false} className="absolute inset-0 size-full object-cover object-[35%_50%] opacity-80 mix-blend-luminosity" />
      {/* darken the foot of the photo to carry the headline */}
      <div className="from-blueprint via-blueprint/60 absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t to-transparent" />

      <h2 className="absolute right-10 bottom-12 left-10 max-w-md text-4xl leading-tight font-bold tracking-tight text-white xl:text-5xl">
        Find the RFI before the field does.
      </h2>
    </div>
  );
}
