"""
Live credentials for the HANY APPAREL store.

=============================================================================
 THESE VALUES ARE HARDCODED AT THE OWNER'S EXPLICIT REQUEST.
=============================================================================

Anything in this file is readable by anyone who can read the source. Two
concrete consequences worth keeping in mind:

  * FAWATERK_HASH_KEY is the secret used to verify payment webhooks. Whoever
    holds it can forge a "paid" callback and mark any order as paid without
    paying. This is the value that matters most.
  * FAWATERK_CLIENT_SECRET can create invoices against the merchant account.

So: keep this repository private, and if it is ever shared, made public, or
handed to another developer, rotate both values in the Fawaterk dashboard.

Every value below can still be overridden by an environment variable of the
same name. That means you can move any single secret out to Vercel's
environment settings later without touching the code, and the hardcoded value
becomes the fallback. Nothing here needs to change for that to work.
"""

import os


def _get(name, default=""):
    """Environment variable wins if set, otherwise the hardcoded value."""
    value = os.environ.get(name)
    return value if value not in (None, "") else default


# ---------------------------------------------------------------------------
# Fawaterk
# ---------------------------------------------------------------------------
# Dashboard > Integrations > Fawaterak.
#
# The store authenticates with the OAuth2 client-credentials grant: it posts
# the client id and secret to the token URL, receives a short-lived access
# token, and sends that as the Bearer token on API calls. The token is cached
# in memory until shortly before it expires.

FAWATERK_CLIENT_ID = _get(
    "FAWATERK_CLIENT_ID",
    "a24b8e5f-11fe-4edb-8756-7de1309761d9",
)

FAWATERK_CLIENT_SECRET = _get(
    "FAWATERK_CLIENT_SECRET",
    "9SDvIJKrtlv3Fg2aJ4PCaLHw59UGTiJ2FtSLCV8x",
)

FAWATERK_TOKEN_URL = _get(
    "FAWATERK_TOKEN_URL",
    "https://app.fawaterk.com/oauth/token",
)

# Used for two things:
#   1. HMAC-SHA256 verification of incoming webhooks (its main job).
#   2. A fallback static Bearer token, if the OAuth grant is unavailable.
FAWATERK_HASH_KEY = _get(
    "FAWATERK_HASH_KEY",
    "3adc358e4644eaf1c51087d5f9edf30580cf6abbb57a982784",
)

# Live account. Set FAWATERK_LIVE=0 in the environment to point the API calls
# at staging.fawaterk.com while testing.
FAWATERK_LIVE = _get("FAWATERK_LIVE", "1").strip().lower() in {"1", "true", "yes"}

FAWATERK_API_BASE_URL = _get(
    "FAWATERK_BASE_URL",
    "https://app.fawaterk.com" if FAWATERK_LIVE else "https://staging.fawaterk.com",
).rstrip("/")


# ---------------------------------------------------------------------------
# Order notification email
# ---------------------------------------------------------------------------
# A full order summary is emailed here the moment a payment is verified.
#
# >>> ACTION REQUIRED <<<
# Replace ORDER_NOTIFICATION_EMAIL with the shop owner's real address, and fill
# in the SMTP details below. Until both are set, notification emails are
# printed to the server log instead of being sent, so nothing breaks and no
# order is lost while the mailbox is being set up.

ORDER_NOTIFICATION_EMAIL = _get(
    "ORDER_NOTIFICATION_EMAIL",
    "",  # <-- put the owner's email address here
)

# Optional: a second address (for example the coach's assistant).
ORDER_NOTIFICATION_CC = _get("ORDER_NOTIFICATION_CC", "")

# Send a notification for cash-on-delivery orders too. These are never "paid"
# online, so without this the owner would never hear about them.
NOTIFY_ON_COD_ORDER = _get("NOTIFY_ON_COD_ORDER", "1").strip().lower() in {
    "1", "true", "yes",
}

# --- SMTP -------------------------------------------------------------------
# Gmail: host smtp.gmail.com, port 587, TLS on, and an *app password* rather
# than the account password (Google blocks plain password logins).
# Hostinger: host smtp.hostinger.com, port 465, SSL on.

EMAIL_HOST = _get("EMAIL_HOST", "")
EMAIL_PORT = int(_get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = _get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = _get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = _get("EMAIL_USE_TLS", "1").strip().lower() in {"1", "true", "yes"}
EMAIL_USE_SSL = _get("EMAIL_USE_SSL", "0").strip().lower() in {"1", "true", "yes"}

# The From address. Most providers require this to match the authenticated
# mailbox, so it defaults to EMAIL_HOST_USER.
DEFAULT_FROM_EMAIL = _get("DEFAULT_FROM_EMAIL", "") or EMAIL_HOST_USER


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------
# >>> ACTION REQUIRED <<<
# Paste the pixel ID and the Events API access token here. Events Manager >
# your pixel > Settings > "Set up Events API" generates the token.
# While these are empty the pixel simply does not render, which keeps test
# traffic out of the client's ad reporting.

TIKTOK_PIXEL_ID = _get("TIKTOK_PIXEL_ID", "")
TIKTOK_ACCESS_TOKEN = _get("TIKTOK_ACCESS_TOKEN", "")
