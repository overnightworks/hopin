"""Sign-in against Keycloak through webauth, and the session gate.

webauth runs the OIDC code flow and serves the start, callback, logout and back-channel logout routes (`sign_in.py`
installs them); HopIn defines none of them, only the `/login` and `/no-access` pages, what a refused sign-in shows
and `/account` (`account.py`), which sends a signed-in person to the issuer's account console. The pages come from the
page build. The logout answer is webauth's own page under its own policy, which admits the issuer's origin for that one
form.

Each sign-in, logout and back-channel entry runs in one transaction of webauth's single-process stores: it holds the
one guard that every access to the accounts and sessions shares from start to end, so no other request sees or changes
them meanwhile, and it restores them when it fails, so a back-channel logout that fails answers 400 and ends no
session. Admission needs the group `REALM_LOGIN_GROUP` names; the group `REALM_ADMIN_GROUP` names and below is the
admin role. The redirect URI is `HOPIN_PUBLIC_ORIGIN` plus the callback path, and the host check admits only that
origin's host. The session cookie is `Secure` and `SameSite=Lax`, and it is the only cookie HopIn sets once sign-in has
finished.

A refused sign-in logs `sign_in.refused` with webauth's closed reason. The one field that carries what a browser sent is
the identity provider's error code from the callback, which webauth keeps only as a short code-shaped token; whatever a
client puts into it reaches the log. `account.changed` carries the person's Keycloak id as `sub` and the role, never a
username.
"""
