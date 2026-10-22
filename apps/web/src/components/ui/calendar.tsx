import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { DayPicker, getDefaultClassNames } from "react-day-picker";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** shadcn's Calendar (react-day-picker v9), themed by the app's tokens. */
function Calendar({ className, classNames, showOutsideDays = true, ...props }: React.ComponentProps<typeof DayPicker>) {
  const d = getDefaultClassNames();
  return (
    <DayPicker
      showOutsideDays={showOutsideDays}
      className={cn("bg-background p-3 [--cell-size:--spacing(8)]", className)}
      classNames={{
        root: cn("w-fit", d.root),
        months: cn("relative flex flex-col gap-4 md:flex-row", d.months),
        month: cn("flex w-full flex-col gap-4", d.month),
        nav: cn("absolute inset-x-0 top-0 flex w-full items-center justify-between gap-1", d.nav),
        button_previous: cn(buttonVariants({ variant: "ghost" }), "size-(--cell-size) p-0 select-none aria-disabled:opacity-50", d.button_previous),
        button_next: cn(buttonVariants({ variant: "ghost" }), "size-(--cell-size) p-0 select-none aria-disabled:opacity-50", d.button_next),
        month_caption: cn("flex h-(--cell-size) w-full items-center justify-center px-(--cell-size)", d.month_caption),
        caption_label: cn("text-sm font-medium select-none", d.caption_label),
        table: "w-full border-collapse",
        weekdays: cn("flex", d.weekdays),
        weekday: cn("text-muted-foreground flex-1 rounded-md text-[0.8rem] font-normal select-none", d.weekday),
        week: cn("mt-2 flex w-full", d.week),
        day: cn("group/day relative aspect-square h-full w-full p-0 text-center select-none", d.day),
        day_button: cn(
          buttonVariants({ variant: "ghost" }),
          "size-(--cell-size) p-0 font-normal aria-selected:opacity-100",
          "group-data-[selected=true]/day:bg-primary group-data-[selected=true]/day:text-primary-foreground",
          d.day_button,
        ),
        today: cn("bg-accent text-accent-foreground rounded-md data-[selected=true]:rounded-none", d.today),
        outside: cn("text-muted-foreground aria-selected:text-muted-foreground", d.outside),
        disabled: cn("text-muted-foreground opacity-50", d.disabled),
        hidden: cn("invisible", d.hidden),
        ...classNames,
      }}
      components={{
        Chevron: ({ orientation, className: c, ...rest }) =>
          orientation === "left" ? (
            <ChevronLeftIcon className={cn("size-4", c)} {...rest} />
          ) : (
            <ChevronRightIcon className={cn("size-4", c)} {...rest} />
          ),
      }}
      {...props}
    />
  );
}

export { Calendar };
