import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { UserDto } from "@cdip/shared";
import { api } from "../api";
import { Avatar } from "../components/AppShell";
import { Card, Notice, PageHeader, PrimaryButton, TextField } from "../components/ui";
import { useAppStore } from "../store";

/** Profile + password, both scoped to the signed-in user (PATCH /auth/me). */
export function AccountPage() {
  const user = useAppStore((s) => s.user);
  const setUser = useAppStore((s) => s.setUser);

  if (!user) return null;

  return (
    <div className="h-full overflow-y-auto px-6 py-8 lg:px-10"><div className="mx-auto max-w-2xl">
      <PageHeader title="Account" subtitle="Update your details and password." />

      <Card className="mb-4">
        <div className="flex items-center gap-4">
          <Avatar name={user.name} size={56} />
          <div className="min-w-0">
            <p className="truncate text-lg font-semibold text-ink">{user.name}</p>
            <p className="truncate text-sm text-ink-muted">{user.email}</p>
            {user.company && <p className="truncate text-sm text-ink-muted">{user.company}</p>}
          </div>
        </div>
      </Card>

      <ProfileForm user={user} onSaved={setUser} />
        <PasswordForm />
      </div>
    </div>
  );
}

function ProfileForm({ user, onSaved }: { user: UserDto; onSaved: (u: UserDto) => void }) {
  // Legacy accounts have only `name`; seed the split fields from it.
  const [first, last] = user.name.split(/\s+(.*)/);
  const [firstName, setFirstName] = useState(user.firstName ?? first ?? "");
  const [lastName, setLastName] = useState(user.lastName ?? last ?? "");
  const [company, setCompany] = useState(user.company ?? "");
  const [email, setEmail] = useState(user.email);

  const save = useMutation({
    mutationFn: () =>
      api.updateProfile({
        firstName: firstName.trim(),
        lastName: lastName.trim(),
        company: company.trim() || null,
        email: email.trim().toLowerCase() === user.email ? undefined : email.trim(),
      }),
    onSuccess: onSaved,
  });

  return (
    <Card className="mb-4" title="Profile">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="First name" value={firstName} onChange={setFirstName} required />
          <TextField label="Last name" value={lastName} onChange={setLastName} />
        </div>
        <TextField label="Company" value={company} onChange={setCompany} placeholder="Optional" />
        <TextField label="Email" type="email" value={email} onChange={setEmail} required />

        {save.isSuccess && <Notice tone="ok">Profile updated.</Notice>}
        {save.isError && (
          <Notice tone="error">
            {(save.error as Error).message.includes("409")
              ? "That email is already in use."
              : (save.error as Error).message}
          </Notice>
        )}
        <PrimaryButton disabled={save.isPending || !firstName.trim()}>
          {save.isPending ? "Saving…" : "Save changes"}
        </PrimaryButton>
      </form>
    </Card>
  );
}

function PasswordForm() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");

  const change = useMutation({
    mutationFn: () => api.changePassword({ currentPassword, newPassword }),
    onSuccess: () => {
      setCurrentPassword("");
      setNewPassword("");
      setConfirm("");
    },
  });

  const mismatch = confirm.length > 0 && confirm !== newPassword;

  return (
    <Card title="Password" subtitle="Your session stays signed in after a change.">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!mismatch) change.mutate();
        }}
      >
        <TextField
          label="Current password"
          type="password"
          value={currentPassword}
          onChange={setCurrentPassword}
          autoComplete="current-password"
          required
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="New password"
            type="password"
            value={newPassword}
            onChange={setNewPassword}
            autoComplete="new-password"
            minLength={8}
            required
          />
          <TextField
            label="Confirm new password"
            type="password"
            value={confirm}
            onChange={setConfirm}
            autoComplete="new-password"
            minLength={8}
            required
          />
        </div>

        {mismatch && <Notice tone="error">The two new passwords don't match.</Notice>}
        {change.isSuccess && <Notice tone="ok">Password changed.</Notice>}
        {change.isError && (
          <Notice tone="error">
            {(change.error as Error).message.includes("403")
              ? "That current password is incorrect."
              : (change.error as Error).message}
          </Notice>
        )}
        <PrimaryButton disabled={change.isPending || mismatch}>
          {change.isPending ? "Updating…" : "Change password"}
        </PrimaryButton>
      </form>
    </Card>
  );
}
