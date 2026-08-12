import nodemailer, { type Transporter } from "nodemailer";
import { env } from "./env.js";
import type { MailMessage } from "./mailTemplates.js";

/**
 * Outbound email: password-reset links and support tickets.
 *
 * With no SMTP_HOST configured the mailer does not fail — it logs the whole
 * message, reset link included, to the server console. That keeps local
 * development and CI working with no mail server, matching how the OCR and
 * malware-scan hooks degrade elsewhere in this codebase.
 *
 * Sending is deliberately never allowed to break the request that triggered
 * it: a password reset still consumes its token, and a support ticket is still
 * persisted, if the mail server is down. The caller decides what to tell the
 * user; `sendMail` only reports whether it went out.
 */

let transporter: Transporter | null = null;

export function mailEnabled(): boolean {
  return Boolean(env.SMTP_HOST);
}

function getTransporter(): Transporter | null {
  if (!env.SMTP_HOST) return null;
  if (!transporter) {
    transporter = nodemailer.createTransport({
      host: env.SMTP_HOST,
      port: env.SMTP_PORT,
      secure: env.SMTP_SECURE,
      auth:
        env.SMTP_USER && env.SMTP_PASSWORD
          ? { user: env.SMTP_USER, pass: env.SMTP_PASSWORD }
          : undefined,
    });
  }
  return transporter;
}

/** Returns true when the message was handed to an SMTP server. */
export async function sendMail(message: MailMessage): Promise<boolean> {
  const transport = getTransporter();
  if (!transport) {
    // Not an error: this is the documented no-SMTP path.
    console.log(
      `[mail] SMTP not configured — not sending. To: ${message.to}\n` +
        `       Subject: ${message.subject}\n` +
        message.text.replace(/^/gm, "       "),
    );
    return false;
  }
  try {
    await transport.sendMail({
      from: env.MAIL_FROM,
      to: message.to,
      subject: message.subject,
      text: message.text,
      html: message.html,
      replyTo: message.replyTo,
    });
    console.log(`[mail] sent "${message.subject}" to ${message.to}`);
    return true;
  } catch (err) {
    console.error(`[mail] FAILED to send "${message.subject}" to ${message.to}:`, err);
    return false;
  }
}


// Templates live next door (pure, unit-tested); re-exported for callers.
export * from "./mailTemplates.js";
