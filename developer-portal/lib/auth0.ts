import { Auth0Client } from "@auth0/nextjs-auth0/server";

// One client for the whole app. `audience` makes Auth0 issue a JWT access token for the Emithran API
// (without it the token is opaque and the backend could not verify it). The SDK serves /auth/login, /auth/callback and
// /auth/logout, keeps the session in an encrypted httpOnly cookie, and refreshes tokens on its own.
export const auth0 = new Auth0Client({
  authorizationParameters: { audience: process.env.AUTH0_AUDIENCE, scope: "openid profile email offline_access" },
});
