/**
 * Email bodies. Pure string building — no transport, no env — so the escaping
 * and the wording can be unit-tested without a mail server.
 */

export interface MailMessage {
  to: string;
  subject: string;
  /** Plain-text body — always sent, so the mail is readable anywhere. */
  text: string;
  /** Optional HTML alternative. */
  html?: string;
  /** Set on support mail so a reply goes to the person who wrote it. */
  replyTo?: string;
}

/** Minimal HTML escaping — user-supplied text goes into these bodies. */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const layout = (heading: string, body: string) => `
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:520px;margin:0 auto;padding:24px;color:#1c2024">
  <h1 style="font-size:18px;margin:0 0 16px">${escapeHtml(heading)}</h1>
  ${body}
  <p style="margin-top:24px;font-size:12px;color:#6b7280">ArcAligned AI</p>
</div>`;

export function passwordResetMail(to: string, name: string, link: string, ttlMinutes: number) {
  const text = [
    `Hi ${name},`,
    "",
    "Someone asked to reset the password for your ArcAligned AI account.",
    "Open this link to choose a new one:",
    "",
    link,
    "",
    `The link works once and expires in ${ttlMinutes} minutes.`,
    "If this wasn't you, ignore this email — your password is unchanged.",
  ].join("\n");

  return {
    to,
    subject: "Reset your ArcAligned AI password",
    text,
    html: layout(
      "Reset your password",
      `<p style="font-size:14px;line-height:1.6">Hi ${escapeHtml(name)}, someone asked to reset
         the password for your ArcAligned AI account.</p>
       <p style="margin:24px 0">
         <a href="${escapeHtml(link)}"
            style="background:#3b2f8f;color:#fff;padding:10px 18px;border-radius:6px;
                   text-decoration:none;font-size:14px;font-weight:600">Choose a new password</a>
       </p>
       <p style="font-size:13px;color:#6b7280;line-height:1.6">
         The link works once and expires in ${ttlMinutes} minutes.<br>
         If this wasn't you, ignore this email — your password is unchanged.
       </p>
       <p style="font-size:12px;color:#9ca3af;word-break:break-all">${escapeHtml(link)}</p>`,
    ),
  } satisfies MailMessage;
}

export function supportTicketMail(
  to: string,
  ticket: { id: string; name: string; email: string; subject: string; message: string },
) {
  const text = [
    `New support ticket from ${ticket.name} <${ticket.email}>`,
    `Ticket: ${ticket.id}`,
    "",
    ticket.message,
  ].join("\n");

  return {
    to,
    // Prefixed so replies thread and inbox rules can match.
    subject: `[Support] ${ticket.subject}`,
    text,
    replyTo: ticket.email,
    html: layout(
      ticket.subject,
      `<p style="font-size:13px;color:#6b7280">From ${escapeHtml(ticket.name)}
         &lt;${escapeHtml(ticket.email)}&gt; · ticket ${escapeHtml(ticket.id)}</p>
       <p style="font-size:14px;line-height:1.6;white-space:pre-wrap">${escapeHtml(ticket.message)}</p>`,
    ),
  } satisfies MailMessage;
}

export function supportAckMail(to: string, name: string, subject: string) {
  const text = [
    `Hi ${name},`,
    "",
    "Thanks — we've got your message and will reply to this address.",
    "",
    `Subject: ${subject}`,
  ].join("\n");

  return {
    to,
    subject: "We received your message",
    text,
    html: layout(
      "We received your message",
      `<p style="font-size:14px;line-height:1.6">Hi ${escapeHtml(name)}, thanks — we've got your
         message and will reply to this address.</p>
       <p style="font-size:13px;color:#6b7280">Subject: ${escapeHtml(subject)}</p>`,
    ),
  } satisfies MailMessage;
}
