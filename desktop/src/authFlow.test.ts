import { describe, expect, it, vi } from "vitest";
import { runDeepLinkLogin, type DeepLinkDeps } from "./authFlow";
import type { AuthStatus, LoginStart } from "./components/LoginScreen";

const authenticated: AuthStatus = { state: "authenticated", authenticated: true };

type Harness = {
  deps: DeepLinkDeps;
  capturedCodeHandler: (code: string) => Promise<void>;
  browserUrl: string | null;
  delivered: string[];
  authenticated: number;
  failures: string[];
  stopped: number;
};

/** Wire the flow with recording fakes; `emitCode` simulates the shell's
 * deep-link handler firing the auth-code event. */
function harness(start: LoginStart, opts: { inShell?: boolean; deliverError?: Error } = {}): Harness {
  const h: Harness = {
    browserUrl: null,
    delivered: [],
    authenticated: 0,
    failures: [],
    stopped: 0,
    capturedCodeHandler: async () => undefined,
    deps: {
      startLogin: vi.fn(async () => start),
      inShell: () => opts.inShell ?? true,
      openBrowser: vi.fn(async (url: string) => {
        h.browserUrl = url;
      }),
      onAuthCode: vi.fn(async (handler) => {
        h.capturedCodeHandler = handler;
        return () => {
          h.stopped += 1;
        };
      }),
      deliverCode: vi.fn(async (code: string) => {
        if (opts.deliverError) throw opts.deliverError;
        h.delivered.push(code);
        return authenticated;
      }),
      onAuthenticated: vi.fn(() => {
        h.authenticated += 1;
      }),
      onFailed: vi.fn((message: string) => {
        h.failures.push(message);
      }),
    },
  };
  return h;
}

describe("runDeepLinkLogin (#20: sign-in drives the deep-link flow)", () => {
  it("opens the browser at the runtime's URL and completes when the code arrives", async () => {
    const h = harness({ state: "awaiting_browser", browser_url: "https://bee.verse4.pet/login?device_code=dc_1" });
    const stop = await runDeepLinkLogin(h.deps);
    // The browser URL came from the runtime, not from any direct login call.
    expect(h.browserUrl).toBe("https://bee.verse4.pet/login?device_code=dc_1");
    expect(h.authenticated).toBe(0); // still waiting for the deep link

    await h.capturedCodeHandler("oc_123");
    expect(h.delivered).toEqual(["oc_123"]); // POST /auth/deliver-code
    expect(h.authenticated).toBe(1);
    expect(h.stopped).toBe(1); // the listener unsubscribed itself
    stop();
    expect(h.stopped).toBe(1); // abort after completion is a no-op
  });

  it("registers the deep-link listener before the browser opens", async () => {
    const order: string[] = [];
    const h = harness({ state: "awaiting_browser", browser_url: "https://x/login" });
    h.deps.onAuthCode = async () => {
      order.push("listen");
      return () => undefined;
    };
    h.deps.openBrowser = async () => {
      order.push("open");
    };
    await runDeepLinkLogin(h.deps);
    expect(order).toEqual(["listen", "open"]);
  });

  it("completes immediately in mock mode without opening a browser", async () => {
    const h = harness({ state: "authenticated", authenticated: true } as LoginStart & AuthStatus);
    await runDeepLinkLogin(h.deps);
    expect(h.authenticated).toBe(1);
    expect(h.browserUrl).toBeNull();
    expect(h.deps.openBrowser).not.toHaveBeenCalled();
  });

  it("refuses to start the browser flow outside the shell", async () => {
    const h = harness({ state: "awaiting_browser", browser_url: "https://x/login" }, { inShell: false });
    await runDeepLinkLogin(h.deps);
    expect(h.browserUrl).toBeNull();
    expect(h.failures).toHaveLength(1);
    expect(h.failures[0]).toContain("not the desktop shell");
  });

  it("reports a failed code exchange instead of pretending to be signed in", async () => {
    const h = harness({ state: "awaiting_browser", browser_url: "https://x/login" },
      { deliverError: new Error("BadRequest: code exchange did not authenticate") });
    await runDeepLinkLogin(h.deps);
    await h.capturedCodeHandler("oc_bad");
    expect(h.authenticated).toBe(0);
    expect(h.failures).toEqual(["BadRequest: code exchange did not authenticate"]);
  });

  it("a refused login start surfaces the runtime's error", async () => {
    const h = harness({ state: "awaiting_browser", browser_url: "" });
    h.deps.startLogin = async () => {
      throw new Error("BadRequest: real-mode login is driven by the shell deep-link handler");
    };
    await runDeepLinkLogin(h.deps);
    expect(h.failures).toHaveLength(1);
    expect(h.browserUrl).toBeNull();
  });

  it("ignores a second code after the first one settled", async () => {
    const h = harness({ state: "awaiting_browser", browser_url: "https://x/login" });
    await runDeepLinkLogin(h.deps);
    await h.capturedCodeHandler("oc_1");
    await h.capturedCodeHandler("oc_2");
    expect(h.delivered).toEqual(["oc_1"]);
    expect(h.authenticated).toBe(1);
  });

  it("abort stops listening and swallows a late code", async () => {
    const h = harness({ state: "awaiting_browser", browser_url: "https://x/login" });
    const stop = await runDeepLinkLogin(h.deps);
    stop();
    expect(h.stopped).toBe(1);
    await h.capturedCodeHandler("oc_late");
    expect(h.delivered).toEqual([]);
    expect(h.authenticated).toBe(0);
  });
});
