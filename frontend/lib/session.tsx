"use client";

/**
 * Who is signed in, for the whole app.
 *
 * The access token lives in `lib/api/client` (memory, never storage) and this
 * holds the human-readable half: name, email, which businesses the person
 * belongs to, and which one the panel is currently showing.
 *
 * On mount it calls `refreshSession()` once. That single request answers both
 * "is there a session?" and "whose?", using the httpOnly cookie the browser
 * sends on its own. Until it comes back the state is `loading` - and the
 * distinction between `loading` and `signed out` matters, because treating
 * the first as the second bounces every returning user to the login page for
 * a frame before letting them in.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  api,
  onSessionEnded,
  refreshSession,
  setAccessToken,
  type LoginBody,
  type SessionPayload,
} from "./api/client";

export type Membership = SessionPayload["memberships"][number];
export type Role = "owner" | "tenant_admin" | "operator";

/** Roles that may change configuration. Mirrors the backend's
 * `MANAGES_CONFIGURATION`; an operator can read and use, not reconfigure. */
export const MANAGES_CONFIGURATION: Role[] = ["owner", "tenant_admin"];
export const OWNS_THE_ACCOUNT: Role[] = ["owner"];

export type SessionStatus = "loading" | "authenticated" | "anonymous";

interface SessionState {
  status: SessionStatus;
  user: SessionPayload["user"] | null;
  memberships: Membership[];
  activeTenantId: string | null;
  /** The caller's role in the active tenant. Null for a platform admin. */
  role: Role | null;
  isPlatformAdmin: boolean;
  /** A platform admin with no business of their own: the panel has
   * nothing to render for them, so they are sent to `/admin`. */
  onlyPlatformAdmin: boolean;
  signIn: (credentials: LoginBody) => Promise<void>;
  signOut: () => Promise<void>;
  /** Re-read the session, e.g. after accepting an invitation. */
  reload: () => Promise<void>;
}

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [user, setUser] = useState<SessionPayload["user"] | null>(null);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [activeTenantId, setActiveTenantId] = useState<string | null>(null);

  const apply = useCallback((session: SessionPayload | null) => {
    if (session === null) {
      setAccessToken(null, null);
      setUser(null);
      setMemberships([]);
      setActiveTenantId(null);
      setStatus("anonymous");
      return;
    }
    setAccessToken(session.access_token, session.active_tenant_id ?? null);
    setUser(session.user);
    setMemberships(session.memberships);
    setActiveTenantId(session.active_tenant_id ?? null);
    setStatus("authenticated");
  }, []);

  // Restore on first paint. Runs once; React 18's double-invoke in
  // development is harmless because `refreshSession` shares one in-flight
  // promise.
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    void refreshSession().then(apply);
  }, [apply]);

  // The client tells us when a refresh definitively failed mid-session, which
  // is the only way a signed-in user becomes anonymous without clicking
  // anything: their refresh token expired, or it was revoked.
  useEffect(() => {
    onSessionEnded(() => {
      apply(null);
      router.replace("/login");
    });
    return () => onSessionEnded(null);
  }, [apply, router]);

  const signIn = useCallback(
    async (credentials: LoginBody) => {
      apply(await api.auth.login(credentials));
    },
    [apply],
  );

  const signOut = useCallback(async () => {
    try {
      await api.auth.logout();
    } catch {
      // The server may already consider us gone. Signing out locally is the
      // part the user asked for, and it must happen either way.
    }
    apply(null);
    router.replace("/login");
  }, [apply, router]);

  const reload = useCallback(async () => {
    apply(await refreshSession());
  }, [apply]);

  const value = useMemo<SessionState>(() => {
    const active = memberships.find((m) => m.tenant_id === activeTenantId);
    return {
      status,
      user,
      memberships,
      activeTenantId,
      role: (active?.role as Role | undefined) ?? null,
      // Read from the session, never inferred. "No active tenant" was the
      // first guess and it was wrong: the operator who registers like any
      // customer and is then promoted has both a tenant and the flag, so the
      // guess sent them to their own panel and left `/admin` unreachable.
      isPlatformAdmin: user?.is_platform_admin ?? false,
      // True only when there is nothing else to show them.
      onlyPlatformAdmin: (user?.is_platform_admin ?? false) && activeTenantId === null,
      signIn,
      signOut,
      reload,
    };
  }, [status, user, memberships, activeTenantId, signIn, signOut, reload]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const context = useContext(SessionContext);
  if (context === null) {
    throw new Error("useSession must be used inside <SessionProvider>");
  }
  return context;
}

/**
 * Whether the current user holds one of these roles.
 *
 * Guards in the panel are a courtesy, not a security boundary: they stop
 * somebody being shown a button that will 403. The backend decides, on every
 * request, from the database - see `require_roles` in `app/api/deps.py`.
 */
export function useHasRole(...roles: Role[]): boolean {
  const { role } = useSession();
  return role !== null && roles.includes(role);
}
