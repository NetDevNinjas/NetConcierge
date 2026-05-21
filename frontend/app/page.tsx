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
  clear: "🗑️",
};

export default function Home() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const feedRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [events]);

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
        </div>
      </header>

      {/* Event Feed */}
      <div ref={feedRef} style={styles.feed}>
        {events.length === 0 && (
          <div style={styles.empty}>
            <p style={{ fontSize: "1.2rem", marginBottom: "0.5rem" }}>
              Waiting for agent activity...
            </p>
            <p style={{ color: "#8b949e", fontSize: "0.85rem" }}>
              Events will appear here when the network or perk agent takes
              action.
            </p>
          </div>
        )}
        {events.map((event) => (
          <div key={event.id} style={styles.eventCard}>
            <div style={styles.eventHeader}>
              <span style={styles.icon}>
                {TYPE_ICONS[event.type] || "•"}
              </span>
              <span
                style={{
                  ...styles.source,
                  color: SOURCE_COLORS[event.source] || "#8b949e",
                }}
              >
                {event.source}
              </span>
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
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    maxWidth: "900px",
    margin: "0 auto",
    padding: "0 1rem",
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
  feed: {
    flex: 1,
    overflowY: "auto",
    padding: "1rem 0",
    display: "flex",
    flexDirection: "column",
    gap: "0.75rem",
  },
  empty: {
    textAlign: "center" as const,
    marginTop: "4rem",
    color: "#8b949e",
  },
  eventCard: {
    background: "#161b22",
    border: "1px solid #30363d",
    borderRadius: "8px",
    padding: "0.75rem 1rem",
    animation: "fadeIn 0.3s ease",
  },
  eventHeader: {
    display: "flex",
    alignItems: "center",
    gap: "0.5rem",
    marginBottom: "0.4rem",
  },
  icon: {
    fontSize: "1rem",
  },
  source: {
    fontWeight: 600,
    fontSize: "0.8rem",
    textTransform: "uppercase" as const,
    letterSpacing: "0.5px",
  },
  eventType: {
    fontSize: "0.75rem",
    color: "#8b949e",
    background: "#1c2128",
    padding: "2px 6px",
    borderRadius: "4px",
    border: "1px solid #30363d",
  },
  timestamp: {
    marginLeft: "auto",
    fontSize: "0.75rem",
    color: "#8b949e",
  },
  message: {
    fontSize: "0.9rem",
    lineHeight: 1.5,
    color: "#e6edf3",
    whiteSpace: "pre-wrap" as const,
  },
  dataBlock: {
    marginTop: "0.5rem",
    padding: "0.5rem",
    background: "#0d1117",
    border: "1px solid #30363d",
    borderRadius: "4px",
    fontSize: "0.75rem",
    color: "#8b949e",
    overflow: "auto",
    maxHeight: "200px",
  },
};
