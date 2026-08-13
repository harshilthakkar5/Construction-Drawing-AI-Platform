import { describe, expect, it } from "vitest";
import { escapeHtml, passwordResetMail, supportTicketMail } from "./mailTemplates.js";
import { hashToken, isWellFormed, tokensMatch, TOKEN_TTL_MINUTES } from "./tokens.js";

/**
 * A reset token is a bearer credential for someone's account, so the rules
 * around it are worth pinning: what is stored is never the token itself, junk
 * never reaches Redis, and comparisons don't leak timing.
 */

const REAL = "a".repeat(64);

describe("token shape", () => {
  it("accepts a 64-char lowercase hex token", () => {
    expect(isWellFormed(REAL)).toBe(true);
    expect(isWellFormed("0123456789abcdef".repeat(4))).toBe(true);
  });

  it("rejects anything else before it can reach Redis", () => {
    expect(isWellFormed("")).toBe(false);
    expect(isWellFormed("short")).toBe(false);
    expect(isWellFormed("A".repeat(64))).toBe(false); // uppercase
    expect(isWellFormed("g".repeat(64))).toBe(false); // not hex
    expect(isWellFormed("a".repeat(65))).toBe(false);
    expect(isWellFormed(null)).toBe(false);
    expect(isWellFormed(12345)).toBe(false);
    // A key-injection attempt must not be treated as a token.
    expect(isWellFormed("../session:abc")).toBe(false);
  });
});

describe("hashToken", () => {
  it("never returns the token itself — that is the point of storing a hash", () => {
    const hash = hashToken(REAL);
    expect(hash).not.toBe(REAL);
    expect(hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it("is deterministic, so a lookup finds the row it wrote", () => {
    expect(hashToken(REAL)).toBe(hashToken(REAL));
  });

  it("separates different tokens", () => {
    expect(hashToken(REAL)).not.toBe(hashToken("b".repeat(64)));
  });
});

describe("tokensMatch", () => {
  it("matches identical values and rejects others", () => {
    expect(tokensMatch(REAL, REAL)).toBe(true);
    expect(tokensMatch(REAL, "b".repeat(64))).toBe(false);
  });

  it("handles different lengths without throwing", () => {
    // timingSafeEqual throws on length mismatch — the guard must come first.
    expect(() => tokensMatch(REAL, "short")).not.toThrow();
    expect(tokensMatch(REAL, "short")).toBe(false);
  });
});

describe("reset email", () => {
  const link = "https://app.example.com/?reset=" + REAL;

  it("carries the link in both the text and HTML bodies", () => {
    const mail = passwordResetMail("user@example.com", "Pavan", link, TOKEN_TTL_MINUTES);
    expect(mail.text).toContain(link);
    expect(mail.html).toContain(link);
    expect(mail.to).toBe("user@example.com");
  });

  it("states the expiry, so the link's limits are not a surprise", () => {
    const mail = passwordResetMail("user@example.com", "Pavan", link, 60);
    expect(mail.text).toContain("60 minutes");
    expect(mail.text.toLowerCase()).toContain("once");
  });

  it("tells an unexpecting recipient that nothing has changed", () => {
    const mail = passwordResetMail("user@example.com", "Pavan", link, 60);
    expect(mail.text).toMatch(/wasn't you/i);
  });
});

describe("escaping", () => {
  it("neutralises HTML in names and messages", () => {
    expect(escapeHtml('<script>alert("x")</script>')).toBe(
      "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;",
    );
  });

  it("escapes user text in a support notification", () => {
    const mail = supportTicketMail("support@example.com", {
      id: "t1",
      name: "<b>Bob</b>",
      email: "bob@example.com",
      subject: "Help",
      message: "<img src=x onerror=alert(1)>",
    });
    expect(mail.html).not.toContain("<img src=x");
    expect(mail.html).toContain("&lt;img src=x");
    // Replying to the notification must reach the person who wrote it.
    expect(mail.replyTo).toBe("bob@example.com");
  });
});
