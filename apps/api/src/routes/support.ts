import { Router } from "express";
import { z } from "zod";
import { currentUser } from "../auth.js";
import { prisma } from "../db.js";
import { sanitizeText } from "../sanitize.js";

/**
 * Support form. Tickets are persisted (support_tickets) and logged; there is
 * no outbound mail from the API — a mailer/webhook can read the table.
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
  res.status(201).json({ id: ticket.id, createdAt: ticket.createdAt });
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
