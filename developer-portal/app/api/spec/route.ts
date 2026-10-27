import spec from "@/content/openapi.json";
export const dynamic = "force-static";
export function GET() { return Response.json(spec); }
