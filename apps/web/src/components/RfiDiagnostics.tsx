import { useMutation } from "@tanstack/react-query";
import { FileArchiveIcon } from "lucide-react";
import { api } from "@/api";
import { Spinner } from "@/components/shared";
import { Button } from "@/components/ui/button";

/**
 * "Diagnostics": the zip the worker writes when RFI_DIAGNOSTICS=on — every
 * prompt, image, reply and decision of one run, for checking WHY the AI said
 * what it said. A run recorded without the setting has no zip; the button
 * says so instead of failing quietly.
 */
export function RfiDiagnosticsButton({
  projectId,
  kind,
  runId,
}: {
  projectId: string;
  kind: "full-scan" | "review";
  runId: string;
}) {
  const fetchLink = useMutation({
    mutationFn: () => api.getRfiDiagnostics(projectId, kind, runId),
    onSuccess: ({ downloadUrl }) => {
      window.location.assign(downloadUrl);
    },
  });
  return (
    <div className="flex flex-col gap-1">
      <Button
        size="sm"
        variant="ghost"
        className="self-start"
        disabled={fetchLink.isPending}
        onClick={() => fetchLink.mutate()}
        title="Everything the AI was given and returned for this run: prompts, images, replies, decisions and the final RFI output"
      >
        {fetchLink.isPending ? <Spinner /> : <FileArchiveIcon />}
        Diagnostics (.zip)
      </Button>
      {fetchLink.error && <p className="text-muted-foreground text-xs">{(fetchLink.error as Error).message}</p>}
    </div>
  );
}
