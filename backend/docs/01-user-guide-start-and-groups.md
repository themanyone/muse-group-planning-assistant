# User guide I — Start + groups

*Chapter 1 of the user guide (slice B-182). For anyone joining or
starting a group — no technical knowledge assumed. Every task
below names the steps, what you should see, what can go wrong,
and how to undo it. Where a screen name is given, it is the name
the app's screens use for the same action the API performs; the
API operation is named once, in brackets, so a helper can find
the exact door.*

## 1. Create your account

**Task: get an account and be signed in.**

1. Open the app and choose **Register**.
   [`POST /api/v1/auth/register`]
2. Enter your email, your name as the group should see it, and a
   password. Choose your **timezone** — the app shows event times
   in the timezone of the group and in yours, so set yours true.
3. Your password must be at least **12 characters** and must not
   be a common or breached password (the app checks a local list
   and refuses in plain words, naming the reason). A short phrase
   you have never used elsewhere is a good shape.
4. You should see: you are signed in straight away — registering
   creates the account and the first session together.

*What can go wrong:* an email already registered is refused —
sign in instead (next task). Too many sign-in attempts in a row
slow down and then pause: repeated wrong passwords trigger a
progressive lockout, so stop guessing and use the magic-link or
reset road below.

*Undo:* there is nothing to undo in registering. Leaving a group
is §5; asking for everything the app holds about you is covered
in the admin + operator guide (B-188) and your own data view
[`GET /api/v1/users/me/data`].

## 2. Sign in — three roads, one account

### 2a. Password

1. Choose **Sign in**, enter email + password.
   [`POST /api/v1/auth/login`]
2. If two-factor authentication is on for your account (§3),
   you will **not** get a session yet: you get a five-minute,
   single-use challenge. Enter the current code from your
   authenticator app to finish. [`POST /api/v1/auth/login/mfa`]

### 2b. Magic link (no password needed)

1. Choose **Email me a sign-in link** and enter your email.
   [`POST /api/v1/auth/magic-link`]
2. The answer is always the same — "sent" — whether or not the
   email has an account. That sameness is deliberate: the app
   never confirms who has an account to someone asking.
3. Open the link within **30 minutes**; it works **once**.
   [`POST /api/v1/auth/magic-link/consume`] A magic link is a
   full sign-in even when two-factor is on: proving you control
   the account's mailbox is itself a factor.

### 2c. Staying signed in, and signing out

Signing in gives a short-lived access token (15 minutes) plus a
refresh token (30 days) that quietly renews it. Signing out
revokes the whole refresh-token family — every token descended
from that sign-in dies together. [`POST /api/v1/auth/logout`]
Re-using an old, already-rotated refresh token revokes the
family too: that is the app treating the reuse as theft, on
purpose.

**Forgot your password?** Choose **Reset password**, enter your
email (same always-"sent" answer), and follow the link within
its window. Setting the new password revokes **all** your
existing sessions — sign in again on each device.
[`POST /api/v1/auth/password-reset`]

## 3. Two-factor authentication (MFA)

**Task: make a stolen password useless on its own.**

1. In your account security settings, start MFA setup.
   [`POST /api/v1/users/me/mfa/setup`] The setup secret is shown
   **once, here** — add it to your authenticator app now.
2. Confirm with a current code from the app.
   [`POST /api/v1/users/me/mfa/confirm`] Nothing protects the
   account until this confirming code proves the app read the
   secret.
3. From then on, password sign-in asks for a code (§2a). Each
   code step can be used once only.

Owners and admins are **strongly urged** to turn MFA on — the
app's status view says plainly whether your roles make that
offer one you should take. [`GET /api/v1/users/me/mfa`]

*Turning it off:* requires a current code, and revokes every
session including the one you are using.
[`POST /api/v1/users/me/mfa/disable`]

*Honest boundary:* this build has **no MFA recovery codes**.
If you lose the authenticator and cannot produce a code, the
password road stays shut; the magic-link road (§2b) still signs
you in, and an account owner can then re-do setup. Do not let
anyone tell you a recovery code exists here — it does not.

## 4. Your profile, password, email, devices, and keys

- **Profile:** change your name, timezone, or locale at any
  time. [`PATCH /api/v1/users/me`]
- **Password change:** needs your current password; every
  existing session is revoked. [`POST /api/v1/users/me/password`]
- **Email change:** the confirmation goes to the **new**
  address — the change completes only when that mailbox proves
  itself. [`POST /api/v1/users/me/email-change`]
- **Devices:** your session list names every device signed in
  as you, newest first, with the current one marked. Revoke one
  device and only that device signs out; the rest keep working.
  [`GET /api/v1/users/me/sessions`,
  `DELETE /api/v1/users/me/sessions/{family_id}`] Signing in
  from a new device also sends you a notice.
- **API keys** (for tools and agents acting as you): a key has a
  label and scopes — `read`, `draft`, `publish`, `admin`. The
  full key is shown **exactly once**, at creation; later lists
  show only its prefix and when it was last used. Revoking is
  immediate. [`POST /api/v1/users/me/api-keys`] No key scope
  lets anyone approve review-queue drafts in your name beyond
  the role law in §6 — approval is a person's act.

## 5. Create a group

**Task: start a group and be its owner.**

1. Choose **New group** and give it a name (required), a
   description if you like, its **home timezone**, and its
   usual meeting place if it has one. [`POST /api/v1/groups`]
2. Pick its visibility:
   - **Private** (the default) — invisible to anyone not in it;
     a private group answers "not found" to non-members, so its
     very existence does not leak.
   - **Invite** — listed, so people can find it exists; joining
     still needs an invitation.
   - **Public-listed** — its public events are visible to
     anyone; member-only material never is.
3. You should see: the group, with you as its **owner**. If two
   groups would share the same web slug, the app settles the
   collision — names may repeat, addresses may not.

*Undo:* an owner can leave or hand over only while another
active owner remains — **the last-owner rule**: a group must
never be left ownerless, and the app refuses the move that
would do it, in plain words.

## 6. Roles — who can do what, in plain words

Every member holds exactly one role in each group. Roles are a
ladder; each rung includes everything below it.

| Role | In plain words |
|---|---|
| **Guest** | Sees only what they were invited to — usually one event. |
| **Member** | Takes part: discusses, RSVPs, claims tasks, votes, adds sources. |
| **Organiser** | Runs plans: approves the review queue (at the group's default setting), publishes events, manages the day-to-day. |
| **Admin** | Runs the group itself: members, settings, integrations, seats. |
| **Owner** | Everything, including managing other owners and erasing the group — with the last-owner rule above standing guard. |

An **agent seat** is not a sixth role: a seat is an AI
participant holding one of these same roles, named by its seat
label in the audit trail, and it can **never approve** — a seat
holding an organiser role may draft and publish, but the
approval act itself is refused to it in the service, not just
hidden on a screen.

*Changing roles:* only an owner may move someone into or out of
the owner and admin rungs; other role changes follow the same
door. [`PATCH /api/v1/groups/{id}/members/{user_id}/role`]

## 7. Invite people, accept, decline, leave

**Task: bring a person into the group.**

1. Choose **Invite**, enter their email, and pick their starting
   role (member, unless you have reason otherwise).
   [`POST /api/v1/groups/{id}/invites`]
2. What happens next depends on them:
   - A person with no account gets a **claimable, passwordless
     account** created by the invite itself — accepting the
     invite and having an account are one flow, not two.
   - **Accept** makes the membership active.
     [`POST /api/v1/groups/{id}/invites/accept`]
   - **Decline** is a settled answer, not a deletion grudge —
     they can be invited again.
     [`POST /api/v1/groups/{id}/invites/decline`]
3. Only **active** memberships count — an invited person who
   has not accepted cannot see a private group and holds no
   role in it yet.

*Removing and leaving:* an organiser-or-above door removes a
member [`DELETE /api/v1/groups/{id}/members/{user_id}`], and
anyone may leave a group themselves
[`POST /api/v1/groups/{id}/leave`] — subject, for owners, to
the last-owner rule in §5.

## 8. Group settings — the dials a member should know exist

Group settings are changed by admins
[`PATCH /api/v1/groups/{id}`]; the fuller settings pack is read
by organisers and written by admins
[`GET/PUT /api/v1/groups/{id}/settings`]. The dials:

- **Name, description, visibility, home timezone, usual place**
  — §5's choices, changeable later.
- **Review rules** — which role may approve (default:
  organiser) and how many distinct approvers a draft needs
  (default: 1). Raising the count means a draft stays pending,
  visibly, until enough different people have approved; the
  same person approving twice is refused.
- **Reminder rules and the weekly digest** — when reminders
  fire and when the digest goes out; chapter VI (B-187) covers
  using them.
- **AI daily budget** — a cap, in the group's settings, on what
  the group's AI work may cost per day; at the cap, extraction
  pauses and says so in plain words rather than failing.
- **Template set, upload quota, source retention** — which
  starter templates the group offers, how much storage its
  sources may use, and how long sources are kept. Retention and
  erasure consequences are spelled out in the admin guide
  (B-188) before any instruction there.

*What you should see:* every settings change is written to the
group's audit trail with the old and new values — a settings
screen never changes anything silently.

## 9. Your notification preferences and quiet hours

These are **yours, per group** — the group's settings decide
what exists; your preferences decide what reaches you.
[`GET/PUT /api/v1/users/me/groups/{group_id}/notification-preferences`]

- **Switches:** for each kind of news — `invite`, `reminder`,
  `change`, `digest`, `rsvp` — and each channel — `email`,
  `in_app`, `messenger` — you may turn delivery on or off. A
  switch you have never touched counts as **on**.
- **Both channels off** for a kind means that kind is not sent
  to you at all, and the suppression itself is audited — it is
  a policy outcome, never a silent drop.
- **Quiet hours:** set a window (start and end, in **your**
  timezone). A non-urgent reminder that would land inside it is
  **held** — its send time moves to the end of the quiet
  window; it is never dropped. Urgent news — a cancellation is
  the standing example — is never held.
- **Messenger honesty:** the Messenger channel exists as a
  preference and a delivery attempt, but in this build its
  driver reports `driver_unavailable` and the notification
  completes through your other channels. The guide says so here
  rather than letting a switch promise a road that is not open.

*Undo:* preferences take effect for notifications created
**after** the change; a notification already queued under the
old policy keeps the policy it was created with — that is the
creation-time law, and it is why a change can look one message
late. It is not lost mail.

## 10. Your first success — the setup checklist

A non-technical reader who has done only this chapter should be
able to stop here, set up:

- [ ] Register, with a 12-character-or-longer password.
- [ ] Turn on two-factor authentication (owners/admins: do not
      skip this).
- [ ] Check your timezone is yours.
- [ ] Create a group (or accept your invitation), and note its
      visibility.
- [ ] Invite one person and watch them accept.
- [ ] Set your quiet hours in that group.

The next chapter (B-183) is giving the app things to read.
