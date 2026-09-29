/**
 * #20: the deep-link login flow, as a pure function over injected effects so
 * it can be tested without the shell.
 *
 * The flow (docs/auth-deep-link.md): the button never calls a "login" that
 * authenticates in place. It starts the login (real mode answers
 * {state:'awaiting_browser', browser_url}), opens the system browser, waits
 * for the shell's deep-link handler to capture skilyst://callback?code=...,
 * and delivers that code to the runtime, which exchanges it (PKCE) for the
 * AK/SK pair stored in the OS keychain.
 */
import type { AuthStatus, LoginStart } from "./components/LoginScreen";

export type DeepLinkDeps = {
  /** POST /auth/login on the runtime. */
  startLogin: () => Promise<LoginStart>;
  /** True when running inside the Tauri shell (deep links can be captured). */
  inShell: () => boolean;
  /** Open the system browser at the runtime's login URL. */
  openBrowser: (url: string) => Promise<void>;
  /** Subscribe to the shell's auth-code event; returns an unsubscribe. */
  onAuthCode: (handler: (code: string) => Promise<void>) => Promise<() => void>;
  /** POST /auth/deliver-code with the one-time code from the deep link. */
  deliverCode: (code: string) => Promise<AuthStatus>;
  /** Fired once the delivered code authenticated the runtime. */
  onAuthenticated: () => void;
  /** Fired when the flow cannot continue; the login screen shows the message. */
  onFailed: (message: string) => void;
};

/** Start the deep-link login. Resolves as soon as the browser is open (the
 * completion arrives later through onAuthenticated / onFailed). Returns an
 * abort function that stops listening — call it when the screen unmounts. */
export async function runDeepLinkLogin(deps: DeepLinkDeps): Promise<() => void> {
  let start: LoginStart;
  try {
    start = await deps.startLogin();
  } catch (exc) {
    deps.onFailed(exc instanceof Error ? exc.message : String(exc));
    return () => undefined;
  }
  if (start.state === "authenticated") {
    // Mock mode (SKILYST_MOCK_AUTH=1): the runtime simulated the browser
    // step inside the call — nothing to wait for.
    deps.onAuthenticated();
    return () => undefined;
  }
  if (!deps.inShell()) {
    deps.onFailed(
      "This window is not the desktop shell, so the browser callback cannot " +
        "be captured. Open the desktop shell, or start the runtime with " +
        "SKILYST_MOCK_AUTH=1 to simulate the browser step.",
    );
    return () => undefined;
  }
  let settled = false;
  let stopListening: () => void = () => undefined;
  let alreadyStopped = false;
  const stopOnce = () => {
    if (!alreadyStopped) {
      alreadyStopped = true;
      stopListening();
    }
  };
  try {
    // Register the listener BEFORE the browser opens: a fast callback (the
    // user is already signed in to the console) must not outrun us.
    stopListening = await deps.onAuthCode(async (code) => {
      if (settled) return; // one code completes one login
      settled = true;
      try {
        await deps.deliverCode(code);
        deps.onAuthenticated();
      } catch (exc) {
        deps.onFailed(exc instanceof Error ? exc.message : String(exc));
      } finally {
        stopOnce();
      }
    });
    await deps.openBrowser(start.browser_url);
  } catch (exc) {
    stopOnce();
    deps.onFailed(exc instanceof Error ? exc.message : String(exc));
  }
  return () => {
    settled = true; // a late code after abort must not deliver
    stopOnce();
  };
}
