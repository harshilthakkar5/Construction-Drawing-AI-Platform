import { Router } from "express";
import { z } from "zod";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { env } from "../env.js";
import { sendMail, supportAckMail, supportTicketMail } from "../mailer.js";
import { sanitizeText } from "../sanitize.js";

/**
 * Support form. The ticket is persisted first and is the record of truth; the
 * email is a notification on top. Mail is therefore best-effort and never
 * fails the request — losing the message because a mail server hiccuped would
 * be far worse than a missed notification, and the row is still queryable.
 *
 * With SUPPORT_EMAIL unset (or no SMTP at all) tickets are stored and logged
 * exactly as before.
 */
export const supportRouter = Router();

const ticketSchema = z.object({
  name: z.string().min(1).max(200),
  email: z.string().email().max(320),
  subject: z.string().min(1).max(300),
  message: z.string().min(1).max(10_000),
});

supportRouter.post("/", async (req, res) => {
  const body = ticketSchema.parse(req.body);
  const user = currentUser(req);
  const ticket = await prisma.supportTicket.create({
    data: {
      userId: user.id,
      name: sanitizeText(body.name),
      email: body.email.toLowerCase(),
      subject: sanitizeText(body.subject),
      message: sanitizeText(body.message),
    },
  });
  console.log(`[support] ticket ${ticket.id} from ${ticket.email}: ${ticket.subject}`);

  // Answer as soon as it is stored; notifying is not the user's problem.
  res.status(201).json({ id: ticket.id, createdAt: ticket.createdAt });

  if (env.SUPPORT_EMAIL) {
    // replyTo is the submitter, so answering the notification reaches them.
    void sendMail(supportTicketMail(env.SUPPORT_EMAIL, ticket));
  }
  void sendMail(supportAckMail(ticket.email, ticket.name, ticket.subject));
});

/** The signed-in user's own tickets (most recent first). */
supportRouter.get("/", async (req, res) => {
  const user = currentUser(req);
  const tickets = await prisma.supportTicket.findMany({
    where: { userId: user.id },
    orderBy: { createdAt: "desc" },
    take: 50,
  });
  res.json(tickets);
});
