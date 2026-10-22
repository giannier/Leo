"use client";

/**
 * Equipo (HU-06).
 *
 * Three things this screen has to get right, and they are the three that go
 * wrong in every team-management page:
 *
 * - **The seat count is visible before you invite, not after.** A trial has
 *   two. Discovering the limit by being refused at the end of a form is the
 *   version of this that generates a support ticket.
 * - **Disabling somebody revokes their sessions immediately.** The screen
 *   says so, because the whole reason anybody presses that button in a hurry
 *   is that they need it to be true right now.
 * - **Roles are described by what they can do**, not by their names. Nobody
 *   outside this codebase knows what `tenant_admin` means.
 */

import { useCallback, useEffect, useState } from "react";

import { AlertIcon, CheckIcon, ClockIcon } from "@/components/icons";
import { Alert, Button, Card, Field, Select, Skeleton, cx } from "@/components/ui";
import { api, type Team, type TeamInvitation, type TeamMember } from "@/lib/api/client";
import { ApiError, humanMessage } from "@/lib/api/errors";
import { OWNS_THE_ACCOUNT, useHasRole, useSession } from "@/lib/session";

/** Mirrors `INVITATION_TTL_DAYS` in `app/services/team.py`. */
const INVITATION_DAYS = 7;

const ROLES = [
  {
    value: "operator",
    label: "Operador",
    what: "Lee y responde conversaciones. No puede cambiar la configuración del agente.",
  },
  {
    value: "tenant_admin",
    label: "Administrador",
    what: "Todo lo anterior, más configurar el agente, el catálogo y el equipo.",
  },
] as const;

const ROLE_LABELS: Record<string, string> = {
  owner: "Dueño",
  tenant_admin: "Administrador",
  operator: "Operador",
};

export default function TeamPage() {
  const { user } = useSession();
  const isOwner = useHasRole(...OWNS_THE_ACCOUNT);

  const [team, setTeam] = useState<Team | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setTeam(await api.team.get());
    } catch (caught) {
      setError(humanMessage(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-heading text-2xl font-semibold text-ink">Equipo</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Las personas que pueden entrar a este panel y atender tus conversaciones.
        </p>
      </header>

      {error ? (
        <Alert tone="danger">
          {error}{" "}
          <button
            type="button"
            onClick={() => void load()}
            className="cursor-pointer font-semibold text-accent underline"
          >
            Reintentar
          </button>
        </Alert>
      ) : null}

      {team === null && !error ? <Skeleton className="h-48 w-full" /> : null}

      {team ? (
        // The people on the left; on the right the seats left and the form to add one.
        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <div className="space-y-6">
            <Members
              members={team.members}
              currentUserId={user?.id ?? ""}
              canManage={isOwner}
              onChanged={load}
            />
            <Invitations invitations={team.invitations} canManage={isOwner} onChanged={load} />
          </div>
          <div className="space-y-6">
            <Seats seats={team.seats} />
            {isOwner ? <InviteForm seats={team.seats} onInvited={load} /> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ----------------------------------------------------------------- seats */

function Seats({ seats }: { seats: Team["seats"] }) {
  const full = seats.used >= seats.limit;
  return (
    <div
      className={cx(
        "flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm",
        full ? "border-warning/30 bg-warning-soft" : "border-line bg-surface",
      )}
    >
      <span className="tabular text-ink">
        <span className="font-semibold">
          {seats.used} de {seats.limit}
        </span>{" "}
        {seats.limit === 1 ? "asiento usado" : "asientos usados"}
      </span>
      {full ? (
        <span className="flex items-center gap-1.5 text-xs font-medium text-warning">
          <AlertIcon className="size-3.5" />
          Para sumar más gente necesitas un plan mayor.
        </span>
      ) : null}
    </div>
  );
}

/* --------------------------------------------------------------- members */

function Members({
  members,
  currentUserId,
  canManage,
  onChanged,
}: {
  members: TeamMember[];
  currentUserId: string;
  canManage: boolean;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function change(
    membershipId: string,
    body: { role?: string; status?: string; puede_revisar?: boolean },
  ) {
    setBusy(membershipId);
    setFailure(null);
    try {
      await api.team.updateMember(membershipId, body);
      await onChanged();
    } catch (caught) {
      setFailure(humanMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Personas con acceso">
      {failure ? (
        <div className="mb-4">
          <Alert tone="danger">{failure}</Alert>
        </div>
      ) : null}

      <ul className="divide-y divide-line">
        {members.map((member) => {
          const isMe = member.user_id === currentUserId;
          const isOwnerRow = member.role === "owner";
          const disabled = member.status === "disabled";

          return (
            <li key={member.id} className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
              <span
                className={cx(
                  "flex size-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold",
                  disabled ? "bg-surface-muted text-ink-faint" : "bg-brand text-brand-fg",
                )}
                aria-hidden="true"
              >
                {member.full_name.trim().charAt(0).toUpperCase() || "?"}
              </span>

              <div className="min-w-0 flex-1">
                <p className={cx("text-sm font-medium", disabled ? "text-ink-faint" : "text-ink")}>
                  {member.full_name}
                  {isMe ? <span className="ml-2 text-xs text-ink-faint">(tú)</span> : null}
                </p>
                <p className="truncate text-xs text-ink-faint">{member.email}</p>
              </div>

              <span
                className={cx(
                  "shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold",
                  disabled ? "bg-surface-muted text-ink-faint" : "bg-surface-muted text-ink-soft",
                )}
              >
                {disabled ? "Desactivado" : (ROLE_LABELS[member.role] ?? member.role)}
              </span>

              {/* The owner row has no controls at all. An account with nobody
               * who can administer it is unrecoverable without us, and the
               * quickest way there is letting the owner demote themselves. */}
              {canManage && !isOwnerRow ? (
                <div className="flex shrink-0 items-center gap-2">
                  {!disabled ? (
                    <label className="flex min-h-11 cursor-pointer items-center gap-2 text-xs text-ink-soft">
                      <input
                        type="checkbox"
                        className="size-5 accent-accent"
                        checked={member.puede_revisar}
                        disabled={busy === member.id}
                        onChange={(e) => void change(member.id, { puede_revisar: e.target.checked })}
                      />
                      Revisor
                    </label>
                  ) : null}
                  {!disabled ? (
                    <select
                      aria-label={`Rol de ${member.full_name}`}
                      value={member.role}
                      disabled={busy === member.id}
                      onChange={(e) => void change(member.id, { role: e.target.value })}
                      className="min-h-11 cursor-pointer rounded-lg border border-line-strong bg-surface px-2 text-sm text-ink"
                    >
                      {ROLES.map((role) => (
                        <option key={role.value} value={role.value}>
                          {role.label}
                        </option>
                      ))}
                    </select>
                  ) : null}

                  <Button
                    variant={disabled ? "secondary" : "ghost"}
                    onClick={() =>
                      void change(member.id, { status: disabled ? "active" : "disabled" })
                    }
                    loading={busy === member.id}
                    className="!min-h-11 px-3 text-xs"
                  >
                    {disabled ? "Reactivar" : "Desactivar"}
                  </Button>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>

      {canManage ? (
        <p className="mt-4 text-xs text-ink-faint">
          Un revisor ve las novedades de tu rubro y puede apagar o encender reglas del asistente,
          aunque no configure lo demás. Quien creó la cuenta siempre lo es.
        </p>
      ) : null}
      {canManage ? (
        <p className="mt-2 text-xs text-ink-faint">
          Al desactivar a alguien, sus sesiones se cierran de inmediato: si tiene el panel
          abierto, deja de funcionar en el acto.
        </p>
      ) : null}
    </Card>
  );
}

/* ----------------------------------------------------------- invitations */

function Invitations({
  invitations,
  canManage,
  onChanged,
}: {
  invitations: TeamInvitation[];
  canManage: boolean;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);

  if (invitations.length === 0) return null;

  async function act(id: string, run: () => Promise<unknown>, resent = false) {
    setBusy(id);
    setSent(null);
    try {
      await run();
      if (resent) setSent(id);
      else await onChanged();
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Invitaciones pendientes">
      <ul className="divide-y divide-line">
        {invitations.map((invitation) => (
          <li
            key={invitation.id}
            className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0"
          >
            <ClockIcon className="size-4 shrink-0 text-ink-faint" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-ink">{invitation.email}</p>
              <p className="text-xs text-ink-faint">
                {ROLE_LABELS[invitation.role] ?? invitation.role} · vence el{" "}
                {new Date(invitation.expires_at).toLocaleDateString("es-PE", {
                  day: "numeric",
                  month: "long",
                })}
              </p>
            </div>

            {sent === invitation.id ? (
              <span className="flex shrink-0 items-center gap-1.5 text-xs font-medium text-success">
                <CheckIcon className="size-3.5" />
                Reenviada
              </span>
            ) : canManage ? (
              <div className="flex shrink-0 gap-2">
                <Button
                  variant="secondary"
                  loading={busy === invitation.id}
                  onClick={() =>
                    void act(invitation.id, () => api.team.resendInvitation(invitation.id), true)
                  }
                  className="!min-h-11 px-3 text-xs"
                >
                  Reenviar
                </Button>
                <Button
                  variant="ghost"
                  loading={busy === invitation.id}
                  onClick={() =>
                    void act(invitation.id, () => api.team.revokeInvitation(invitation.id))
                  }
                  className="!min-h-11 px-3 text-xs"
                >
                  Cancelar
                </Button>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/* ------------------------------------------------------------------ form */

function InviteForm({ seats, onInvited }: { seats: Team["seats"]; onInvited: () => Promise<void> }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<string>("operator");
  const [emailError, setEmailError] = useState<string | undefined>();
  const [failure, setFailure] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const full = seats.used >= seats.limit;
  const chosen = ROLES.find((r) => r.value === role);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);
    setSuccess(null);

    if (!email.includes("@")) {
      setEmailError("Escribe un correo válido.");
      return;
    }
    setEmailError(undefined);

    setBusy(true);
    try {
      await api.team.invite({ email: email.trim(), role: role as "operator" | "tenant_admin" });
      setSuccess(email.trim());
      setEmail("");
      await onInvited();
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "seat_limit_reached") {
        setFailure("Ya usaste todos los asientos de tu plan.");
      } else {
        setFailure(humanMessage(caught));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Invitar a alguien"
      description={`Le llega un correo con un enlace que vence en ${INVITATION_DAYS} días.`}
    >
      {/* Outside the branch below, on purpose. Sending the last invitation
       * fills the plan, which swaps the form for the "no seats" notice - and
       * with the confirmation inside the form, the person who just invited
       * somebody would see only the refusal and never learn it worked. */}
      {success ? (
        <div className="mb-4">
          <Alert tone="success" title="Invitación enviada">
            Le escribimos a {success}. Si no le llega, puedes reenviarla desde la lista de arriba.
          </Alert>
        </div>
      ) : null}

      {full ? (
        // Said before the form, not after the submit. Being refused at the end
        // of a form you already filled is what generates the support ticket.
        <Alert tone="warning" title="No te quedan asientos">
          Tu plan incluye {seats.limit}. Puedes desactivar a alguien para liberar uno, o pasar a
          un plan mayor.
        </Alert>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-4">
          {failure ? <Alert tone="danger">{failure}</Alert> : null}

          <Field
            label="Correo de la persona"
            name="invite_email"
            type="email"
            inputMode="email"
            autoCapitalize="none"
            spellCheck={false}
            required
            value={email}
            error={emailError}
            onChange={(e) => setEmail(e.target.value)}
          />

          <Select
            label="¿Qué va a poder hacer?"
            name="invite_role"
            options={ROLES.map((r) => ({ value: r.value, label: r.label }))}
            value={role}
            onChange={(e) => setRole(e.target.value)}
            // The role's powers spelled out under the field. Nobody outside
            // this codebase knows what "tenant_admin" means.
            hint={chosen?.what}
          />

          <Button type="submit" loading={busy} disabled={!email.trim()}>
            Enviar invitación
          </Button>
        </form>
      )}
    </Card>
  );
}
