import { describe, expect, it } from "vitest";
import { hashPassword, resolveProjectAccess, verifyPassword } from "./security.js";

describe("password hashing", () => {
  it("verifies the original password and rejects others", async () => {
    const stored = await hashPassword("correct horse battery staple");
    expect(stored).toMatch(/^[0-9a-f]{32}:[0-9a-f]{128}$/);
    expect(await verifyPassword("correct horse battery staple", stored)).toBe(true);
    expect(await verifyPassword("wrong password", stored)).toBe(false);
  });

  it("salts hashes (same password → different stored values)", async () => {
    expect(await hashPassword("pw-eight")).not.toEqual(await hashPassword("pw-eight"));
  });

  it("rejects malformed stored values instead of throwing", async () => {
    expect(await verifyPassword("x", "not-a-hash")).toBe(false);
    expect(await verifyPassword("x", "")).toBe(false);
  });
});

describe("resolveProjectAccess (owner/member RBAC)", () => {
  const owner = "user-owner";
  const member = "user-member";
  const stranger = "user-stranger";
  const project = { ownerId: owner };

  it("grants owner to the project owner", () => {
    expect(resolveProjectAccess(project, null, owner)).toBe("owner");
  });

  it("grants the membership role to members", () => {
    expect(resolveProjectAccess(project, { role: "member" }, member)).toBe("member");
    expect(resolveProjectAccess(project, { role: "owner" }, member)).toBe("owner");
  });

  it("denies non-members", () => {
    expect(resolveProjectAccess(project, null, stranger)).toBe("none");
  });

  it("treats legacy ownerless projects as owner-access for any user", () => {
    expect(resolveProjectAccess({ ownerId: null }, null, stranger)).toBe("owner");
  });
});
