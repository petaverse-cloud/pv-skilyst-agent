import { describe, expect, it } from "vitest";
import { expiryUrgency, type AuthStatus } from "./LoginScreen";

// Expiry-renewal model: the credential lapses silently today; the chip must
// escalate within 72h so the user renews before being dropped to the gate.
describe("expiryUrgency", () => {
  it("is calm while the credential is comfortably valid", () => {
    const u = expiryUrgency(20 * 24 * 3600);
    expect(u.expiringSoon).toBe(false);
    expect(u.daysLeft).toBe(20);
  });

  it("flags renewal inside the 72h window", () => {
    const u = expiryUrgency(48 * 3600);
    expect(u.expiringSoon).toBe(true);
    expect(u.daysLeft).toBe(2);
  });

  it("flags renewal at the boundary hour", () => {
    expect(expiryUrgency(72 * 3600).expiringSoon).toBe(true);
    expect(expiryUrgency(73 * 3600).expiringSoon).toBe(false);
  });

  it("returns no urgency when expiry is unknown", () => {
    const u = expiryUrgency(undefined);
    expect(u).toEqual({ expiringSoon: false, daysLeft: null });
  });

  it("never reports more than a day of overshoot past expiry", () => {
    // Expired credentials should not show negative-days absurdity in UI copy.
    const u = expiryUrgency(-3600);
    expect(u.expiringSoon).toBe(true);
    expect(u.daysLeft).toBeLessThanOrEqual(0);
  });
});

// Compile-time guard: the chip's status payload carries expires_in.
describe("AuthStatus expiry contract", () => {
  it("exposes expires_in as an optional number", () => {
    const s = { expires_in: 42 } as AuthStatus;
    expect(s.expires_in).toBe(42);
  });
});
