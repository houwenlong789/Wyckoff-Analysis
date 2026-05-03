export const onRequest: PagesFunction = async (context) => {
  const { request } = context

  if (request.method === 'OPTIONS') {
    return new Response(null, {
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
        'Access-Control-Allow-Headers': '*',
        'Access-Control-Max-Age': '86400',
      },
    })
  }

  const targetUrl = request.headers.get('X-Target-URL')
  if (!targetUrl) {
    return Response.json({ error: 'Missing X-Target-URL header' }, { status: 400 })
  }

  const url = new URL(request.url)
  const proxyPath = url.pathname.replace('/api/llm-proxy', '')
  const dest = targetUrl + proxyPath + url.search

  const headers = new Headers()
  for (const [key, value] of request.headers.entries()) {
    if (['host', 'x-target-url', 'connection', 'cf-connecting-ip', 'cf-ray', 'cf-visitor', 'cf-worker'].includes(key)) continue
    headers.set(key, value)
  }
  headers.set('user-agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36')

  try {
    const response = await fetch(dest, {
      method: request.method,
      headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
    })

    const respHeaders = new Headers()
    for (const [key, value] of response.headers.entries()) {
      if (['transfer-encoding', 'content-encoding'].includes(key)) continue
      respHeaders.set(key, value)
    }
    respHeaders.set('Access-Control-Allow-Origin', '*')

    return new Response(response.body, {
      status: response.status,
      headers: respHeaders,
    })
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err)
    return Response.json({ error: { message: `Proxy error: ${msg}` } }, { status: 502 })
  }
}
