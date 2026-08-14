import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/api";
import { Notice, PageHeader, Spinner, TextArea, TextField } from "@/components/shared";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAppStore } from "@/store";

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
    <div className="h-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto flex max-w-2xl flex-col gap-4 md:gap-6">
        <PageHeader
          title="Support"
          subtitle="We're here to help. Fill out the form below and our support team will get back to you as soon as possible."
        />

        <Card>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit.mutate();
            }}
          >
            <CardHeader>
              <CardTitle>Send a message</CardTitle>
              <CardDescription>We reply by email, usually within a working day.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 pt-6">
              <div className="grid gap-4 sm:grid-cols-2">
                <TextField
                  label="Name"
                  value={name}
                  onChange={setName}
                  placeholder="Your name"
                  required
                />
                <TextField
                  label="Email"
                  type="email"
                  value={email}
                  onChange={setEmail}
                  placeholder="Your email"
                  required
                />
              </div>
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
                <Notice tone="ok">
                  Thanks — your message is with the team. We'll reply by email.
                </Notice>
              )}
              {submit.isError && <Notice tone="error">{(submit.error as Error).message}</Notice>}
            </CardContent>
            <CardFooter className="pt-6">
              <Button type="submit" className="w-full" disabled={submit.isPending}>
                {submit.isPending && <Spinner />}
                {submit.isPending ? "Sending…" : "Submit"}
              </Button>
            </CardFooter>
          </form>
        </Card>

        {tickets.data && tickets.data.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Your previous messages</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="divide-y">
                {tickets.data.map((ticket) => (
                  <li key={ticket.id} className="py-3 first:pt-0 last:pb-0">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="truncate font-medium">{ticket.subject}</span>
                      <span className="text-muted-foreground shrink-0 text-xs">
                        {new Date(ticket.createdAt).toLocaleDateString()}
                      </span>
                    </div>
                    <p className="text-muted-foreground mt-1 line-clamp-2 text-sm whitespace-pre-wrap">
                      {ticket.message}
                    </p>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
