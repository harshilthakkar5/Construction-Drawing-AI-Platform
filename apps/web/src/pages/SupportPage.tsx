import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { Card, Notice, PageHeader, PrimaryButton, TextArea, TextField } from "../components/ui";
import { useAppStore } from "../store";

/** Support form; tickets are stored server-side and listed back below. */
export function SupportPage() {
  const user = useAppStore((s) => s.user);
  const queryClient = useQueryClient();
  const [name, setName] = useState(user?.name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");

  const tickets = useQuery({ queryKey: ["support"], queryFn: api.listSupportTickets });

  const submit = useMutation({
    mutationFn: () => api.submitSupport({ name, email, subject, message }),
    onSuccess: () => {
      setSubject("");
      setMessage("");
      void queryClient.invalidateQueries({ queryKey: ["support"] });
    },
  });

  return (
    <div className="h-full overflow-y-auto px-6 py-8 lg:px-10"><div className="mx-auto max-w-2xl">
      <PageHeader
        title="Support"
        subtitle="We're here to help. Fill out the form below and our support team will get back to you as soon as possible."
      />

      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          submit.mutate();
        }}
      >
        <TextField label="Name" value={name} onChange={setName} placeholder="Your name" required />
        <TextField
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          placeholder="Your email"
          required
        />
        <TextField
          label="Subject"
          value={subject}
          onChange={setSubject}
          placeholder="Subject of your message"
          required
        />
        <TextArea
          label="Message"
          value={message}
          onChange={setMessage}
          placeholder="How can we help you?"
          required
        />

        {submit.isSuccess && (
          <Notice tone="ok">Thanks — your message is with the team. We'll reply by email.</Notice>
        )}
        {submit.isError && <Notice tone="error">{(submit.error as Error).message}</Notice>}

        <PrimaryButton className="w-full" disabled={submit.isPending}>
          {submit.isPending ? "Sending…" : "Submit"}
        </PrimaryButton>
      </form>

      {tickets.data && tickets.data.length > 0 && (
        <Card className="mt-8" title="Your previous messages">
          <ul className="divide-y divide-hairline">
            {tickets.data.map((ticket) => (
              <li key={ticket.id} className="py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate font-medium text-ink">{ticket.subject}</span>
                  <span className="shrink-0 text-xs text-ink-muted">
                    {new Date(ticket.createdAt).toLocaleDateString()}
                  </span>
                </div>
                <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-sm text-ink-soft">
                  {ticket.message}
                </p>
              </li>
            ))}
          </ul>
          </Card>
        )}
      </div>
    </div>
  );
}
