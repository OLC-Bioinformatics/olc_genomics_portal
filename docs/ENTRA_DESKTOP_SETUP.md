# Entra ID Desktop GUI Setup

This runbook configures the native Nanopore GUI to sign in with a user's own
Microsoft Entra ID account. No FoodPort password or shared GUI account is
created.

The recommended low-administration option is portal-assisted pairing. It uses
the existing FoodPort web login and does not require a new Entra app
registration for the desktop GUI.

## What is needed

An Entra administrator is not needed for portal-assisted pairing. The backend
implements these endpoints:

```text
POST /en-ca/api/auth/pairing/start/
GET/POST /en-ca/api/auth/pairing/approve/<pairing_id>/
POST /en-ca/api/auth/pairing/exchange/
```

The GUI starts a pairing and opens the returned approval URL in the system
browser. The user signs in through the existing Microsoft login, approves the
pairing, and types the displayed six-digit code into the GUI. The GUI exchanges
the pairing ID and code for a short-lived FoodPort token.

The pairing ID is not a credential. The code is generated only after an
authenticated portal approval, stored only as a hash, expires after five
minutes, and is consumed atomically once.

The separate Entra public-client setup below is only needed if the GUI later
needs direct MSAL authentication without using the portal browser.

## 1. Identify the tenant

In the Azure portal:

1. Open **Microsoft Entra ID**.
2. Open **Overview**.
3. Copy **Tenant ID**.

This should match the existing FoodPort tenant. Do not create a new tenant.

## 2. Register or identify the FoodPort API

Use the app registration that represents the FoodPort backend. If one does not
exist, create one:

1. Open **App registrations** and select **New registration**.
2. Name it `FoodPort API`.
3. Select **Accounts in this organizational directory only**.
4. Do not add a redirect URI.
5. Open **Expose an API**.
6. Set the Application ID URI to the default value, usually:

   ```text
   api://<foodport-api-application-client-id>
   ```

7. Add a delegated scope:
   - Scope name: `nanopore.upload`
   - Who can consent: **Admins and users**, or **Admins only** if required by
     organizational policy
   - Display name: `Use Nanopore processing`
   - Description: `Create and process Nanopore runs in FoodPort`
   - State: **Enabled**

Record these values:

```text
ENTRA_API_AUDIENCE=api://<foodport-api-application-client-id>
ENTRA_API_SCOPE=nanopore.upload
```

The access token audience must be the FoodPort API, not Microsoft Graph.

## 3. Register the desktop GUI

1. In **App registrations**, select **New registration**.
2. Name it `FoodPort Nanopore GUI`.
3. Select **Accounts in this organizational directory only**.
4. Do not create a client secret.
5. Under **Authentication**, add the platform **Mobile and desktop
   applications**.
6. Enable the public-client flow only if the GUI uses device authorization.
7. Under **API permissions**, add:
   - **My APIs**
   - `FoodPort API`
   - Delegated permission `nanopore.upload`
8. Select **Grant admin consent** if organizational policy requires it.

Record the desktop application's **Application (client) ID**:

```text
ENTRA_DESKTOP_CLIENT_ID=<desktop-gui-application-client-id>
```

Never create or distribute a client secret for this desktop application.

## 4. Choose the user authorization policy

The simplest first deployment is an explicit allow-list of Entra object IDs.
For each approved operator:

1. Open the user's Entra profile.
2. Copy **Object ID**.
3. Add the IDs to the backend configuration as a comma-separated value:

```text
ENTRA_DESKTOP_ALLOWED_OBJECT_IDS=<object-id-1>,<object-id-2>
```

Do not use an unrestricted email-domain allow-list for production unless that
is an intentional organizational policy. If a domain policy is approved, it
can be configured as:

```text
ENTRA_DESKTOP_ALLOWED_EMAIL_DOMAINS=inspection.gc.ca
```

Keep automatic local-user creation disabled initially:

```text
ENTRA_DESKTOP_AUTO_CREATE_USERS=false
```

This requires the Entra email to already match an active FoodPort user. After
the first controlled test succeeds, automatic provisioning can be considered
if it has been approved.

## 5. Configure the FoodPort deployment

Set these values in the web container's environment. Use the tenant ID from
step 1 and the application IDs from steps 2 and 3:

```text
ENTRA_TENANT_ID=<tenant-id>
ENTRA_DESKTOP_CLIENT_ID=<desktop-gui-client-id>
ENTRA_API_AUDIENCE=api://<foodport-api-application-client-id>
ENTRA_API_SCOPE=nanopore.upload
ENTRA_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
ENTRA_JWKS_URI=https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys
ENTRA_DESKTOP_ALLOWED_OBJECT_IDS=<approved-object-id-1>,<approved-object-id-2>
ENTRA_DESKTOP_AUTO_CREATE_USERS=false
FOODPORT_DESKTOP_TOKEN_TTL_SECONDS=3600
```

Do not put these values in the GUI. The GUI only needs the desktop client ID,
tenant ID, and API scope. It must never receive a client secret.

Restart the web service after changing environment variables. Verify the
migration is applied:

```bash
python manage.py showmigrations api
```

The output must show:

```text
[X] 0001_initial
[X] 0002_desktoppairing
```

## 6. Test the portal-assisted GUI login

The GUI flow is:

1. POST `/en-ca/api/auth/pairing/start/`.
2. Open the returned `approval_url` in the system browser.
3. The user signs in with Microsoft and presses **Approve**.
4. The user enters the displayed six-digit code into the GUI.
5. POST `/en-ca/api/auth/pairing/exchange/` with `pairing_id` and `code`.
6. Keep the returned FoodPort token in memory only.
7. Use `Authorization: Token <foodport-token>` for Nanopore requests.
8. On sign-out, call `/en-ca/api/auth/entra/logout/` and clear the token.

The pairing exchange is rate limited and returns a generic `401` for invalid,
expired, already-used, or unapproved pairings.

## 7. Optional direct Entra GUI setup

The GUI developer should use MSAL with authorization code plus PKCE or device
authorization. The GUI flow is:

1. User selects **Sign in with Microsoft**.
2. Microsoft authentication completes in the system browser.
3. MSAL returns an access token for the FoodPort API scope.
4. GUI sends that token to `/en-ca/api/auth/entra/exchange/`.
5. GUI stores the returned FoodPort token in memory only.
6. GUI sends `Authorization: Token <foodport-token>` to Nanopore endpoints.
7. On sign-out, GUI calls `/en-ca/api/auth/entra/logout/` and clears the token.

Use a unique run name and one small POD5 file for the first end-to-end test.
Confirm that the user can create only their own run and that another user's run
returns `404`.

## Troubleshooting

- `401 Entra authentication failed`: check issuer, audience, tenant, scope,
  client ID, JWKS URI, and the user's allow-list entry.
- `403` or no permission in Entra: confirm the delegated API permission and
  admin consent.
- Exchange succeeds but Nanopore calls return `401`: confirm the GUI is using
  the returned FoodPort token, not the original Entra bearer token.
- User is rejected as unprovisioned: create an active FoodPort user with the
  same email, or explicitly approve automatic provisioning.
- Never copy access tokens, FoodPort tokens, device codes, or SAS URLs into
  tickets or logs.
