"use client";

import { useEffect, useRef, useState } from "react";

interface AgentEvent {
  id: string;
  source: string;
  type: string;
  message: string;
  data?: Record<string, unknown>;
  timestamp: string;
}

const SOURCE_COLORS: Record<string, string> = {
  "network-agent": "#58a6ff",
  "perk-agent": "#bc8cff",
  system: "#8b949e",
};

const TYPE_ICONS: Record<string, string> = {
  fault_detected: "⚠️",
  tool_call: "🔧",
  tool_result: "📋",
  escalation: "🚨",
  resolved: "✅",
  tier1: "🎁",
  tier2: "🏆",
  info: "ℹ️",
  clear: "📶",
};

function EventCard({ event }: { event: AgentEvent }) {
  return (
    <div style={styles.eventCard}>
      <div style={styles.eventHeader}>
        <span style={styles.icon}>{TYPE_ICONS[event.type] || "•"}</span>
        <span style={styles.eventType}>{event.type}</span>
        <span style={styles.timestamp}>
          {new Date(event.timestamp).toLocaleTimeString()}
        </span>
      </div>
      <div style={styles.message}>{event.message}</div>
      {event.data && (
        <pre style={styles.dataBlock}>
          {JSON.stringify(event.data, null, 2)}
        </pre>
      )}
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div style={styles.empty}>
      <p style={{ color: "#8b949e", fontSize: "0.85rem" }}>
        Waiting for {label} activity...
      </p>
    </div>
  );
}

export default function Home() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const networkRef = useRef<HTMLDivElement>(null);
  const perkRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const evtSource = new EventSource("/api/events");

    evtSource.onopen = () => setConnected(true);
    evtSource.onerror = () => setConnected(false);

    evtSource.onmessage = (e) => {
      try {
        const event: AgentEvent = JSON.parse(e.data);
        if (event.type === "clear") {
          setEvents([]);
        } else {
          setEvents((prev) => [...prev, event]);
        }
      } catch {
        // ignore malformed events
      }
    };

    return () => evtSource.close();
  }, []);

  const networkEvents = events.filter(
    (e) => e.source === "network-agent" || e.source === "system"
  );
  const perkEvents = events.filter(
    (e) => e.source === "perk-agent" || e.source === "system"
  );

  useEffect(() => {
    if (networkRef.current) {
      networkRef.current.scrollTop = networkRef.current.scrollHeight;
    }
  }, [networkEvents.length]);

  useEffect(() => {
    if (perkRef.current) {
      perkRef.current.scrollTop = perkRef.current.scrollHeight;
    }
  }, [perkEvents.length]);

  return (
    <div style={styles.container}>
      {/* Header */}
      <header style={styles.header}>
        <div style={styles.titleRow}>
          <h1 style={styles.title}>CodeFest 4.0</h1>
          <span style={styles.subtitle}>NetConcierge Agent Monitor</span>
        </div>
        <div
          style={{
            ...styles.status,
            color: connected ? "#3fb950" : "#f85149",
          }}
        >
          <span
            style={{
              ...styles.dot,
              backgroundColor: connected ? "#3fb950" : "#f85149",
            }}
          />
          {connected ? "Live" : "Disconnected"}
          <button
            onClick={() => {
              fetch("/api/events", { method: "DELETE" });
              setEvents([]);
            }}
            style={styles.clearBtn}
          >
            Clear
          </button>
        </div>
      </header>

      {/* Two-column layout */}
      <div style={styles.columns}>
        {/* Network Agent Column */}
        <div style={styles.column}>
          <div style={{ ...styles.columnHeader, borderColor: "#58a6ff" }}>
            <span style={{ color: "#58a6ff", fontWeight: 600 }}>
              🌐 Network Agent
            </span>
            <span style={styles.eventCount}>{networkEvents.length}</span>
          </div>
          <div ref={networkRef} style={styles.feed}>
            {networkEvents.length === 0 ? (
              <EmptyState label="network agent" />
            ) : (
              networkEvents.map((event) => (
                <EventCard key={event.id} event={event} />
              ))
            )}
          </div>
        </div>

        {/* Perk Agent Column */}
        <div style={styles.column}>
          <div style={{ ...styles.columnHeader, borderColor: "#bc8cff" }}>
            <span style={{ color: "#bc8cff", fontWeight: 600 }}>
              🎁 Perk Agent
            </span>
            <span style={styles.eventCount}>{perkEvents.length}</span>
          </div>
          <div ref={perkRef} style={styles.feed}>
            {perkEvents.length === 0 ? (
              <EmptyState label="perk agent" />
            ) : (
              perkEvents.map((event) => (
                <EventCard key={event.id} event={event} />
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    padding: "0 1.5rem",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "1.5rem 0 1rem",
    borderBottom: "1px solid #30363d",
  },
  titleRow: {
    display: "flex",
    alignItems: "baseline",
    gap: "1rem",
  },
  title: {
    fontSize: "1.8rem",
    fontWeight: 700,
    background: "linear-gradient(135deg, #58a6ff, #bc8cff)",
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
  },
  subtitle: {
    fontSize: "0.9rem",
    color: "#8b949e",
  },
  status: {
    display: "flex",
    alignItems: "center",
    gap: "0.4rem",
    fontSize: "0.8rem",
    fontWeight: 500,
  },
  dot: {
    width: "8px",
    height: "8px",
    borderRadius: "50%",
    display: "inline-block",
  },
  clearBtn: {
    marginLeft: "0.75rem",
    padding: "4px 10px",
    fontSize: "0.7rem",
    fontWeight: 500,
    color: "#e6edf3",
    background: "#30363d",
    border: "1px solid #484f58",
    borderRadius: "4px",
    cursor: "pointer",
  },
  columns: {
    display: "flex",
    flex: 1,
    gap: "1rem",
    paddingTop: "1rem",
    minHeight: 0,
  },
  column: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
    background: "#0d1117",
    border: "1px solid #30363d",
    borderRadius: "8px",
    overflow: "hidden",
  },
  columnHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "0.75rem 1rem",
    background: "#161b22",
    borderBottom: "2px solid",
    fontSize: "0.85rem",
  },
  eventCount: {
    fontSize: "0.7rem",
    color: "#8b949e",
    background: "#1c2128",
    padding: "2px 8px",
    borderRadius: "10px",
    border: "1px solid #30363d",
  },
  feed: {
    flex: 1,
    overflowY: "auto",
    padding: "0.75rem",
    display: "flex",
    flexDirection: "column",
    gap: "0.5rem",
  },
  empty: {
    textAlign: "center" as const,
    marginTop: "3rem",
    color: "#8b949e",
  },
  eventCard: {
    background: "#161b22",
    border: "1px solid #30363d",
    borderRadius: "6px",
    padding: "0.6rem 0.75rem",
  },
  eventHeader: {
    display: "flex",
    alignItems: "center",
    gap: "0.4rem",
    marginBottom: "0.3rem",
  },
  icon: {
    fontSize: "0.9rem",
  },
  eventType: {
    fontSize: "0.7rem",
    color: "#8b949e",
    background: "#1c2128",
    padding: "1px 5px",
    borderRadius: "3px",
    border: "1px solid #30363d",
  },
  timestamp: {
    marginLeft: "auto",
    fontSize: "0.7rem",
    color: "#8b949e",
  },
  message: {
    fontSize: "0.85rem",
    lineHeight: 1.5,
    color: "#e6edf3",
    whiteSpace: "pre-wrap" as const,
  },
  dataBlock: {
    marginTop: "0.4rem",
    padding: "0.4rem",
    background: "#0d1117",
    border: "1px solid #30363d",
    borderRadius: "4px",
    fontSize: "0.7rem",
    color: "#8b949e",
    overflow: "auto",
    maxHeight: "150px",
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
  },
};
