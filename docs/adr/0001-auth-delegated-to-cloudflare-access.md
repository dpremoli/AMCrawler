# Authentication is delegated to Cloudflare Access

AMCrawler has no login code of its own. Google sign-in happens in Cloudflare Access in front of the tunnel, and the app only verifies the `Cf-Access-Jwt-Assertion` header (signature, audience, expiry) on every request, rejecting anything without a valid token. We chose this over app-level Google OAuth because the owner already runs other services behind Access, it keeps OAuth secrets out of the app, and the JWT check still blocks requests that bypass the tunnel (e.g. from the LAN). Do not add a login page: change the Access policy instead.
