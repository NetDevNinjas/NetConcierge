/**
 * SSE event stream + POST ingestion endpoint.
 *
 * GET  /api/events  → Server-Sent Events stream (browser subscribes here)
 * POST /api/events  → Agents push events here; broadcast to all SSE clients
 */

type SSEClient = {
  id: string;
  controller: ReadableStreamDefaultController;
};

// In-memory event store and client list (process-scoped singleton)
const clients: SSEClient[] = [];
const eventHistory: EventPayload[] = [];
const MAX_HISTORY = 200;

export interface EventPayload {
  id: string;
  source: string;
  type: string;
  message: string;
  data?: Record<string, unknown>;
  timestamp: string;
}

function broadcast(event: EventPayload) {
  eventHistory.push(event);
  if (eventHistory.length > MAX_HISTORY) {
    eventHistory.shift();
  }
  const encoded = `data: ${JSON.stringify(event)}\n\n`;
  for (let i = clients.length - 1; i >= 0; i--) {
    try {
      clients[i].controller.enqueue(new TextEncoder().encode(encoded));
    } catch {
      clients.splice(i, 1);
    }
  }
}

export async function GET() {
  const stream = new ReadableStream({
    start(controller) {
      const clientId = crypto.randomUUID();
      clients.push({ id: clientId, controller });

      // Send event history on connect
      for (const event of eventHistory) {
        const encoded = `data: ${JSON.stringify(event)}\n\n`;
        controller.enqueue(new TextEncoder().encode(encoded));
      }

      // Cleanup on close
      const cleanup = () => {
        const idx = clients.findIndex((c) => c.id === clientId);
        if (idx !== -1) clients.splice(idx, 1);
      };

      // The controller's cancel signal handles cleanup
      (controller as unknown as { signal?: AbortSignal }).signal?.addEventListener(
        "abort",
        cleanup
      );
    },
    cancel() {
      // Stream closed by client
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

export async function POST(request: Request) {
  const body = await request.json();

  const event: EventPayload = {
    id: crypto.randomUUID(),
    source: body.source || "unknown",
    type: body.type || "info",
    message: body.message || "",
    data: body.data || undefined,
    timestamp: body.timestamp || new Date().toISOString(),
  };

  broadcast(event);

  return Response.json({ status: "ok", id: event.id });
}

// Allow clearing events (useful for demo resets)
export async function DELETE() {
  eventHistory.length = 0;
  broadcast({
    id: crypto.randomUUID(),
    source: "system",
    type: "clear",
    message: "Event feed cleared",
    timestamp: new Date().toISOString(),
  });
  return Response.json({ status: "cleared" });
}
