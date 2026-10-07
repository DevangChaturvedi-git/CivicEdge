"""Small HTTP relay used twice:
 * as the WAN link emulator between gateway and city (configurable one-way
   delay, jitter and link-down), and
 * as the node's portal, the single published port in front of core/gateway.
ROUTES is "prefix=url,prefix=url"; the longest matching prefix wins."""
import asyncio, random
import httpx, uvicorn
from fastapi import FastAPI, Request, Response
from .common import env

ROUTES = sorted((tuple(x.split("=", 1)) for x in env("ROUTES", "/=http://city:8730").split(",")),
                key=lambda r: -len(r[0]))
L = {"delay_ms": float(env("DELAY_MS", "0")), "jitter_ms": float(env("JITTER_MS", "0")), "down": False}
CTL_TOKEN = env("INTERNAL_TOKEN", "int")
app = FastAPI()
client = httpx.AsyncClient(timeout=10)

async def hop():
    d = L["delay_ms"] + random.uniform(-L["jitter_ms"], L["jitter_ms"])
    if d > 0:
        await asyncio.sleep(d / 1000)

@app.post("/_wan")
async def wan_ctl(req: Request):
    if req.headers.get("authorization", "") != "Bearer " + CTL_TOKEN:
        return Response(status_code=401)
    L.update({k: v for k, v in (await req.json()).items() if k in L})
    return L

@app.api_route("/{path:path}", methods=["GET", "POST"])
async def relay(path: str, req: Request):
    if L["down"]:
        await asyncio.sleep(0.2)
        return Response(status_code=503, content=b"link down")
    target = next(u for p, u in ROUTES if ("/" + path).startswith(p))
    body = await req.body()
    await hop()
    try:
        r = await client.request(req.method, f"{target}/{path}", params=req.query_params, content=body,
                                 headers={k: v for k, v in req.headers.items()
                                          if k.lower() in ("authorization", "content-type")})
    except httpx.HTTPError:
        return Response(status_code=502, content=b"upstream unreachable")
    await hop()
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type"))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(env("PORT", "8720")), log_level="warning")
