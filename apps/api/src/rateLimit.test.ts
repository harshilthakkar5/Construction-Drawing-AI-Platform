import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./redis.js", () => ({
  redis: { call: vi.fn(async () => [1, 60_000]) },
}));

const saved = { ...process.env };
afterEach(() => {
  process.env = { ...saved };
  vi.resetModules();
});

const load = () => import("./rateLimit.js");

describe("limits are tunable without editing code", () => {
  it("0 disables a tier instead of rejecting everything", async () => {
    // A limiter with limit:0 would reject every request — an operator turning
    // a tier "off" must not take the endpoint down.
    process.env.RATE_LIMIT_CHAT_PER_MINUTE = "0";
    const { chatLimiter } = await load();
    const next = vi.fn();
    (chatLimiter as unknown as (r: unknown, s: unknown, n: () => void) => void)(
      {},
      {},
      next,
    );
    expect(next).toHaveBeenCalledOnce();
  });

  it("a non-numeric value falls back to the default rather than disabling the tier", async () => {
    // Silently disabling a spend limit because someone typed "twenty" is the
    // worst possible reading of a bad value.
    process.env.RATE_LIMIT_CHAT_PER_MINUTE = "twenty";
    const { chatLimiter } = await load();
    const next = vi.fn();
    (chatLimiter as unknown as (r: unknown, s: unknown, n: () => void) => void)(
      {},
      {},
      next,
    );
    // The real limiter needs req/res plumbing; not being the pass-through is
    // what this asserts.
    expect(next).not.toHaveBeenCalledOnce();
  });

  it("every tier is a function the app can mount", async () => {
    const mod = await load();
    for (const tier of [
      mod.generalLimiter,
      mod.authLimiter,
      mod.chatLimiter,
      mod.summaryLimiter,
    ]) {
      expect(typeof tier).toBe("function");
    }
  });
});

describe("the cost-bearing routes are the throttled ones", () => {
  /**
   * The limiters are mounted per-route, not per-router, because these routers
   * also serve GETs — listing portions, reading summaries, quoting a cost.
   * Throttling those at the summary tier (30/hour) would break browsing.
   */
  const source = (path: string) =>
    import("node:fs").then((fs) =>
      fs.readFileSync(new URL(path, import.meta.url), "utf8"),
    );

  it("chat POST is throttled, its GET is not", async () => {
    const file = await source("./routes/chat.ts");
    expect(file).toContain('chatRouter.post("/", chatLimiter');
    expect(file).toMatch(/chatRouter\.get\("\/:sessionId\/messages", async/);
  });

  it("summary POSTs are throttled, the estimate and list GETs are not", async () => {
    const portions = await source("./routes/portions.ts");
    const summaries = await source("./routes/summaries.ts");
    expect(portions).toContain('post("/:portionId/summarize", summaryLimiter');
    expect(portions).toMatch(/get\("\/:portionId\/summarize\/estimate", async/);
    expect(summaries).toContain('post("/project", summaryLimiter');
    expect(summaries).toContain('post("/rebuild", summaryLimiter');
    expect(summaries).toMatch(/get\("\/", async/);
  });

  it("auth is rate limited at the router, since it has no session to key on", async () => {
    const app = await source("./app.ts");
    expect(app).toContain('app.use("/auth", authLimiter, authRouter)');
  });

  it("the general limiter sits AFTER requireAuth so it keys on the user", async () => {
    // Before it, everyone behind one office NAT would share a bucket.
    const app = await source("./app.ts");
    const auth = app.indexOf("app.use(requireAuth)");
    const general = app.indexOf("app.use(generalLimiter)");
    expect(auth).toBeGreaterThan(-1);
    expect(general).toBeGreaterThan(auth);
  });

  it("trusts a proxy hop, or every client collapses into one bucket", async () => {
    const app = await source("./app.ts");
    expect(app).toContain('app.set("trust proxy"');
  });
});
