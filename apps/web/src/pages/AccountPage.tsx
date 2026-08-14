import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { UserDto } from "@cdip/shared";
import { api } from "@/api";
import { InitialsAvatar } from "@/components/AppShell";
import { Notice, PageHeader, Spinner, TextField } from "@/components/shared";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAppStore } from "@/store";

/** Profile + password, both scoped to the signed-in user (PATCH /auth/me). */
export function AccountPage() {
  const user = useAppStore((s) => s.user);
  const setUser = useAppStore((s) => s.setUser);

  if (!user) return null;

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto flex max-w-2xl flex-col gap-4 md:gap-6">
        <PageHeader title="Account" subtitle="Update your details and password." />

        <Card>
          <CardContent className="flex items-center gap-4">
            <InitialsAvatar name={user.name} className="size-14 text-lg" />
            <div className="min-w-0">
              <p className="truncate text-lg font-semibold">{user.name}</p>
              <p className="text-muted-foreground truncate text-sm">{user.email}</p>
              {user.company && (
                <p className="text-muted-foreground truncate text-sm">{user.company}</p>
              )}
            </div>
          </CardContent>
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
    <Card>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <CardHeader>
          <CardTitle>Profile</CardTitle>
          <CardDescription>How your name appears across the workspace.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 pt-6">
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
        </CardContent>
        <CardFooter className="justify-end pt-6">
          <Button type="submit" disabled={save.isPending || !firstName.trim()}>
            {save.isPending && <Spinner />}
            {save.isPending ? "Saving…" : "Save changes"}
          </Button>
        </CardFooter>
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
    <Card>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!mismatch) change.mutate();
        }}
      >
        <CardHeader>
          <CardTitle>Password</CardTitle>
          <CardDescription>Your session stays signed in after a change.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 pt-6">
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
        </CardContent>
        <CardFooter className="justify-end pt-6">
          <Button type="submit" disabled={change.isPending || mismatch}>
            {change.isPending && <Spinner />}
            {change.isPending ? "Updating…" : "Change password"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
