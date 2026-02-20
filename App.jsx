import { useState, useEffect, useCallback, useRef } from "react";
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";

// ─── Constants ────────────────────────────────────────────────────────────────
const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

const SEVERITY_CONFIG = {
  1: { label: "INFO",     color: "#22c55e", bg: "bg-green-500/15",  border: "border-green-500/30",  text: "text-green-400"  },
  2: { label: "WARNING",  color: "#eab308", bg: "bg-yellow-500/15", border: "border-yellow-500/30", text: "text-yellow-400" },
  3: { label: "HIGH",     color: "#f97316", bg: "bg-orange-500/15", border: "border-orange-500/30", text: "text-orange-400" },
  4: { label: "CRITICAL", color: "#ef4444", bg: "bg-red-500/15",    border: "border-red-500/30",    text: "text-red-400"    },
};

const STATUS_CONFIG = {
  open:           { color: "#ef4444", icon: "⚡" },
  mitigated:      { color: "#22c55e", icon: "✅" },
  closed:         { color: "#6b7280", icon: "🔒" },
  false_positive: { color: "#a78bfa", icon: "🚫" },
};

const INCIDENT_TYPE_ICONS = {
  raid:          "🚨",
  report_burst:  "📣",
  spam_flood:    "💬",
  link_flood:    "🔗",
  media_flood:   "🖼️",
  phishing:      "🎣",
  botnet:        "🤖",
  manual:        "🛠️",
};

// ─── API Client ───────────────────────────────────────────────────────────────
function useAPI() {
  const token = localStorage.getItem("shield_token");

  const request = useCallback(async (path, options = {}) => {
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    });
    if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
    return res.json();
  }, [token]);

  return { request };
}

// ─── Mock Data (for demo when API unavailable) ────────────────────────────────
function generateMockIncidents() {
  const types = ["raid", "spam_flood", "link_flood", "botnet", "phishing", "report_burst"];
  const statuses = ["open", "mitigated", "closed", "false_positive"];
  return Array.from({ length: 24 }, (_, i) => ({
    id: 1000 - i,
    group_id: -100123456789,
    incident_type: types[i % types.length],
    severity: ((i % 4) + 1),
    status: statuses[i % statuses.length],
    title: [
      "Rapid join spike detected — possible raid",
      "Link flood from multiple new users",
      "Botnet detected: similar usernames joining",
      "Report burst against user",
      "Media flood from unverified accounts",
      "Phishing links detected and removed",
    ][i % 6],
    risk_score: Math.random() * 0.4 + 0.6,
    auto_mitigated: Math.random() > 0.4,
    created_at: new Date(Date.now() - i * 3600000 * 2).toISOString(),
    evidence: {
      signals: [
        { rule_id: "JOIN_SPIKE", description: "20 joins in 30s" },
        { rule_id: "NEW_ACCOUNT_RATIO", description: "75% new accounts" },
      ],
      ml_score: Math.random() * 0.4 + 0.6,
    },
  }));
}

function generateTimeseriesData() {
  return Array.from({ length: 24 }, (_, i) => ({
    hour: `${String(i).padStart(2, "0")}:00`,
    incidents: Math.floor(Math.random() * 8),
    joins: Math.floor(Math.random() * 120 + 20),
    messages: Math.floor(Math.random() * 800 + 100),
  }));
}

// ─── Components ───────────────────────────────────────────────────────────────

function Badge({ severity }) {
  const cfg = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG[1];
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-bold
                      border ${cfg.bg} ${cfg.border} ${cfg.text}`}>
      {cfg.label}
    </span>
  );
}

function StatusPill({ status }) {
  const cfg = STATUS_CONFIG[status] || { color: "#6b7280", icon: "?" };
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
          style={{ background: cfg.color + "22", color: cfg.color, border: `1px solid ${cfg.color}44` }}>
      {cfg.icon} {status.replace("_", " ")}
    </span>
  );
}

function MetricCard({ label, value, change, icon, color = "#3b82f6" }) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-white/5 bg-white/3 p-6 backdrop-blur-sm
                    hover:border-white/10 transition-all duration-300 group">
      <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-500"
           style={{ background: `radial-gradient(circle at 50% 0%, ${color}12, transparent 70%)` }} />
      <div className="flex items-start justify-between mb-3">
        <span className="text-2xl">{icon}</span>
        {change !== undefined && (
          <span className={`text-xs font-mono ${change >= 0 ? "text-red-400" : "text-green-400"}`}>
            {change >= 0 ? "▲" : "▼"} {Math.abs(change)}%
          </span>
        )}
      </div>
      <div className="text-3xl font-bold font-mono" style={{ color }}>{value}</div>
      <div className="text-xs text-gray-500 mt-1 uppercase tracking-widest">{label}</div>
    </div>
  );
}

function IncidentRow({ incident, onAction, selected, onSelect }) {
  const typeCfg = SEVERITY_CONFIG[incident.severity] || SEVERITY_CONFIG[1];
  return (
    <tr
      onClick={() => onSelect(incident)}
      className={`border-b border-white/5 cursor-pointer transition-colors duration-150
                  ${selected ? "bg-blue-500/8" : "hover:bg-white/3"}`}
    >
      <td className="px-4 py-3 font-mono text-xs text-gray-500">#{incident.id}</td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">{INCIDENT_TYPE_ICONS[incident.incident_type] || "⚡"}</span>
          <span className="text-sm text-white/80 max-w-xs truncate">{incident.title}</span>
        </div>
      </td>
      <td className="px-4 py-3"><Badge severity={incident.severity} /></td>
      <td className="px-4 py-3"><StatusPill status={incident.status} /></td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1">
          <div className="h-1.5 w-20 bg-white/10 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${(incident.risk_score || 0) * 100}%`,
                background: typeCfg.color,
              }}
            />
          </div>
          <span className="text-xs font-mono text-gray-400">
            {((incident.risk_score || 0) * 100).toFixed(0)}%
          </span>
        </div>
      </td>
      <td className="px-4 py-3 text-xs text-gray-500 font-mono">
        {incident.auto_mitigated ? (
          <span className="text-green-400/80">🤖 Auto</span>
        ) : (
          <span className="text-yellow-400/80">👤 Manual</span>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-gray-500 font-mono">
        {new Date(incident.created_at).toLocaleTimeString()}
      </td>
      <td className="px-4 py-3">
        {incident.status === "open" && (
          <div className="flex gap-1">
            <ActionButton label="✅" title="Approve" onClick={(e) => { e.stopPropagation(); onAction(incident.id, "approve_mute"); }} color="green" />
            <ActionButton label="↩️" title="Rollback" onClick={(e) => { e.stopPropagation(); onAction(incident.id, "rollback"); }} color="yellow" />
            <ActionButton label="🚫" title="False Positive" onClick={(e) => { e.stopPropagation(); onAction(incident.id, "false_positive"); }} color="purple" />
          </div>
        )}
      </td>
    </tr>
  );
}

function ActionButton({ label, title, onClick, color }) {
  const colors = {
    green:  "hover:bg-green-500/20 hover:border-green-500/40",
    yellow: "hover:bg-yellow-500/20 hover:border-yellow-500/40",
    purple: "hover:bg-purple-500/20 hover:border-purple-500/40",
    red:    "hover:bg-red-500/20 hover:border-red-500/40",
  };
  return (
    <button
      title={title}
      onClick={onClick}
      className={`px-2 py-1 text-sm rounded border border-white/10 transition-all duration-150 ${colors[color] || ""}`}
    >
      {label}
    </button>
  );
}

function IncidentDetail({ incident, onAction, onClose }) {
  if (!incident) return null;
  const signals = incident.evidence?.signals || [];
  const shap = incident.evidence?.shap_values || {};

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-2xl
                   border border-white/10 bg-[#0d0d14] shadow-2xl p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <button onClick={onClose}
          className="absolute top-4 right-4 text-gray-500 hover:text-white text-xl">✕</button>

        <div className="flex items-start gap-3 mb-6">
          <span className="text-3xl">{INCIDENT_TYPE_ICONS[incident.incident_type]}</span>
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="font-mono text-gray-500 text-sm">#{incident.id}</span>
              <Badge severity={incident.severity} />
              <StatusPill status={incident.status} />
            </div>
            <h2 className="text-white font-semibold">{incident.title}</h2>
            <div className="text-xs text-gray-500 mt-1 font-mono">
              {new Date(incident.created_at).toLocaleString()}
            </div>
          </div>
        </div>

        {/* Risk Score */}
        <div className="mb-6 p-4 rounded-xl bg-white/3 border border-white/5">
          <div className="flex justify-between items-center mb-2">
            <span className="text-xs text-gray-500 uppercase tracking-wider">Combined Risk Score</span>
            <span className="font-mono text-lg font-bold text-orange-400">
              {((incident.risk_score || 0) * 100).toFixed(1)}%
            </span>
          </div>
          <div className="h-2 bg-white/10 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${(incident.risk_score || 0) * 100}%`,
                background: "linear-gradient(90deg, #22c55e, #eab308, #ef4444)",
              }}
            />
          </div>
        </div>

        {/* Triggered Rules */}
        {signals.length > 0 && (
          <div className="mb-6">
            <h3 className="text-xs text-gray-500 uppercase tracking-wider mb-3">Triggered Rules</h3>
            <div className="space-y-2">
              {signals.map((s, i) => (
                <div key={i} className="flex items-start gap-3 p-3 rounded-lg bg-red-500/8 border border-red-500/20">
                  <span className="font-mono text-xs text-red-400 bg-red-500/20 px-2 py-0.5 rounded shrink-0">
                    {s.rule_id}
                  </span>
                  <span className="text-sm text-white/70">{s.description}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* SHAP Values */}
        {Object.keys(shap).length > 0 && (
          <div className="mb-6">
            <h3 className="text-xs text-gray-500 uppercase tracking-wider mb-3">ML Feature Importance (SHAP)</h3>
            <div className="space-y-1.5">
              {Object.entries(shap)
                .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
                .slice(0, 8)
                .map(([feature, value]) => (
                  <div key={feature} className="flex items-center gap-2 text-xs">
                    <span className="w-44 text-gray-400 truncate font-mono">{feature}</span>
                    <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.min(100, Math.abs(value) * 200)}%`,
                          background: value > 0 ? "#ef4444" : "#22c55e",
                        }}
                      />
                    </div>
                    <span className={`w-14 text-right font-mono ${value > 0 ? "text-red-400" : "text-green-400"}`}>
                      {value > 0 ? "+" : ""}{value.toFixed(3)}
                    </span>
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* Actions */}
        {incident.status === "open" && (
          <div className="flex gap-3 pt-4 border-t border-white/5">
            <button onClick={() => { onAction(incident.id, "approve_mute"); onClose(); }}
              className="flex-1 py-2.5 rounded-xl bg-green-500/15 border border-green-500/30
                         text-green-400 text-sm font-medium hover:bg-green-500/25 transition-all">
              ✅ Approve Action
            </button>
            <button onClick={() => { onAction(incident.id, "rollback"); onClose(); }}
              className="flex-1 py-2.5 rounded-xl bg-yellow-500/15 border border-yellow-500/30
                         text-yellow-400 text-sm font-medium hover:bg-yellow-500/25 transition-all">
              ↩️ Rollback
            </button>
            <button onClick={() => { onAction(incident.id, "false_positive"); onClose(); }}
              className="flex-1 py-2.5 rounded-xl bg-purple-500/15 border border-purple-500/30
                         text-purple-400 text-sm font-medium hover:bg-purple-500/25 transition-all">
              🚫 False Positive
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function LiveFeedItem({ event, index }) {
  const colors = { join: "#22c55e", message: "#3b82f6", leave: "#6b7280", report: "#ef4444" };
  const icons = { join: "👤", message: "💬", leave: "🚪", report: "🚨" };
  return (
    <div
      className="flex items-center gap-3 py-2 border-b border-white/5 text-sm animate-fadeIn"
      style={{ animationDelay: `${index * 50}ms` }}
    >
      <span className="text-base">{icons[event.type] || "⚡"}</span>
      <span style={{ color: colors[event.type] || "#fff" }}
            className="font-mono text-xs uppercase w-16 shrink-0">{event.type}</span>
      <span className="text-gray-400 truncate flex-1">{event.description}</span>
      <span className="text-gray-600 font-mono text-xs shrink-0">{event.time}</span>
    </div>
  );
}

function GroupCard({ group, onLockdown, onRelease }) {
  return (
    <div className="rounded-xl border border-white/8 bg-white/3 p-4 hover:border-white/12 transition-all">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="font-semibold text-white/90 truncate">{group.title}</div>
          <div className="text-xs text-gray-500 font-mono mt-0.5">ID: {group.id}</div>
        </div>
        <div className={`w-2 h-2 rounded-full mt-1.5 ${group.lockdown_active ? "bg-red-500 animate-pulse" : "bg-green-500"}`} />
      </div>
      <div className="flex gap-2 mt-3">
        {group.lockdown_active ? (
          <button onClick={() => onRelease(group.id)}
            className="flex-1 py-1.5 text-xs rounded-lg bg-green-500/15 border border-green-500/30
                       text-green-400 hover:bg-green-500/25 transition-all">
            🔓 Release
          </button>
        ) : (
          <button onClick={() => onLockdown(group.id)}
            className="flex-1 py-1.5 text-xs rounded-lg bg-red-500/15 border border-red-500/30
                       text-red-400 hover:bg-red-500/25 transition-all">
            🔒 Lockdown
          </button>
        )}
        <div className={`px-2 py-1.5 text-xs rounded-lg border ${
          group.captcha_active
            ? "bg-blue-500/15 border-blue-500/30 text-blue-400"
            : "bg-white/5 border-white/10 text-gray-500"
        }`}>
          {group.captcha_active ? "🛡️ Captcha ON" : "Captcha OFF"}
        </div>
      </div>
    </div>
  );
}

// ─── Main Dashboard ────────────────────────────────────────────────────────────
export default function ShieldBotDashboard() {
  const [activeTab, setActiveTab] = useState("overview");
  const [incidents, setIncidents] = useState(generateMockIncidents());
  const [groups, setGroups] = useState([
    { id: -100123456789, title: "Crypto Community Hub", lockdown_active: false, captcha_active: true },
    { id: -100987654321, title: "Dev Discussion Group", lockdown_active: false, captcha_active: false },
    { id: -100555444333, title: "NFT Collectors",       lockdown_active: true,  captcha_active: true  },
  ]);
  const [selectedIncident, setSelectedIncident] = useState(null);
  const [timeseries, setTimeseries] = useState(generateTimeseriesData());
  const [liveFeed, setLiveFeed] = useState([]);
  const [filterStatus, setFilterStatus] = useState("all");
  const [filterSeverity, setFilterSeverity] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [notification, setNotification] = useState(null);
  const feedRef = useRef([]);

  // ── Live Feed Simulation ────────────────────────────────────
  useEffect(() => {
    const events = [
      { type: "join",    description: "User @crypto_fan123 joined group Crypto Community Hub" },
      { type: "message", description: "15 messages in 10s — rate spike detected in Crypto Community Hub" },
      { type: "join",    description: "Bot-like account (age: 2 days) joined NFT Collectors" },
      { type: "report",  description: "3rd report filed against @spam_bot_9999" },
      { type: "leave",   description: "User @old_member left Dev Discussion Group" },
      { type: "join",    description: "New account cluster detected — 8 similar usernames" },
    ];
    let idx = 0;
    const interval = setInterval(() => {
      const now = new Date().toLocaleTimeString();
      const newEvent = { ...events[idx % events.length], time: now, id: Date.now() };
      setLiveFeed(prev => [newEvent, ...prev].slice(0, 50));
      idx++;
    }, 2500);
    return () => clearInterval(interval);
  }, []);

  // ── Periodic metrics refresh ────────────────────────────────
  useEffect(() => {
    const interval = setInterval(() => {
      setTimeseries(generateTimeseriesData());
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  const showNotification = (msg, type = "success") => {
    setNotification({ msg, type });
    setTimeout(() => setNotification(null), 3500);
  };

  const handleAction = useCallback((incidentId, action) => {
    setIncidents(prev => prev.map(i => {
      if (i.id !== incidentId) return i;
      const statusMap = {
        approve_mute:   "mitigated",
        rollback:       "closed",
        false_positive: "false_positive",
        close:          "closed",
      };
      return { ...i, status: statusMap[action] || i.status };
    }));
    const labels = {
      approve_mute:   "Action approved ✅",
      rollback:       "Action rolled back ↩️",
      false_positive: "Marked as false positive 🚫",
    };
    showNotification(labels[action] || "Action performed", "success");
  }, []);

  const handleLockdown = (groupId) => {
    setGroups(prev => prev.map(g => g.id === groupId ? { ...g, lockdown_active: true } : g));
    showNotification("🔒 Lockdown activated", "warning");
  };

  const handleRelease = (groupId) => {
    setGroups(prev => prev.map(g => g.id === groupId ? { ...g, lockdown_active: false } : g));
    showNotification("🔓 Lockdown released", "success");
  };

  // ── Filtered Incidents ──────────────────────────────────────
  const filteredIncidents = incidents.filter(i => {
    if (filterStatus !== "all" && i.status !== filterStatus) return false;
    if (filterSeverity !== "all" && String(i.severity) !== filterSeverity) return false;
    if (searchQuery && !i.title.toLowerCase().includes(searchQuery.toLowerCase())) return false;
    return true;
  });

  // ── Metrics Summary ─────────────────────────────────────────
  const openCount     = incidents.filter(i => i.status === "open").length;
  const criticalCount = incidents.filter(i => i.severity === 4 && i.status === "open").length;
  const autoMitigated = incidents.filter(i => i.auto_mitigated).length;
  const fpRate        = incidents.filter(i => i.status === "false_positive").length;

  const TABS = [
    { id: "overview",  label: "Overview",   icon: "📊" },
    { id: "incidents", label: "Incidents",  icon: "🚨" },
    { id: "groups",    label: "Groups",     icon: "👥" },
    { id: "live",      label: "Live Feed",  icon: "⚡" },
  ];

  return (
    <div className="min-h-screen bg-[#08080f] text-white font-sans">
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap');
        * { box-sizing: border-box; }
        body { font-family: 'DM Sans', sans-serif; }
        .font-mono { font-family: 'Space Mono', monospace; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
        .animate-fadeIn { animation: fadeIn 0.3s ease forwards; }
        @keyframes pulse-glow { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        .animate-pulse { animation: pulse-glow 2s ease-in-out infinite; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #ffffff22; border-radius: 2px; }
      `}</style>

      {/* ── Notification Toast ─────────────────────────────── */}
      {notification && (
        <div className={`fixed top-4 right-4 z-[100] px-4 py-3 rounded-xl text-sm font-medium
                         border shadow-2xl animate-fadeIn backdrop-blur-sm
                         ${notification.type === "warning"
                           ? "bg-yellow-500/20 border-yellow-500/40 text-yellow-300"
                           : "bg-green-500/20 border-green-500/40 text-green-300"}`}>
          {notification.msg}
        </div>
      )}

      {/* ── Header ─────────────────────────────────────────── */}
      <header className="border-b border-white/5 bg-black/30 backdrop-blur-md sticky top-0 z-40">
        <div className="max-w-screen-xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600
                            flex items-center justify-center text-sm font-bold shadow-lg shadow-blue-500/30">
              🛡️
            </div>
            <div>
              <span className="font-semibold text-white">ShieldBot</span>
              <span className="ml-2 text-xs text-gray-500 font-mono">Enterprise</span>
            </div>
          </div>

          <nav className="flex items-center gap-1">
            {TABS.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2 rounded-lg text-sm transition-all duration-200 flex items-center gap-2
                            ${activeTab === tab.id
                              ? "bg-white/10 text-white border border-white/10"
                              : "text-gray-500 hover:text-gray-300 hover:bg-white/5"}`}
              >
                <span>{tab.icon}</span>
                <span className="hidden md:inline">{tab.label}</span>
                {tab.id === "incidents" && openCount > 0 && (
                  <span className="bg-red-500 text-white text-xs font-bold rounded-full w-4 h-4
                                   flex items-center justify-center ml-0.5">
                    {openCount > 9 ? "9+" : openCount}
                  </span>
                )}
              </button>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-500/10 border border-green-500/20">
              <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              <span className="text-xs text-green-400 font-mono">LIVE</span>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-screen-xl mx-auto px-6 py-8">

        {/* ══════════════════════════════════════════════════ */}
        {/* OVERVIEW TAB                                       */}
        {/* ══════════════════════════════════════════════════ */}
        {activeTab === "overview" && (
          <div className="space-y-8">
            {/* Metrics Row */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <MetricCard label="Open Incidents" value={openCount}     icon="🚨" color="#ef4444" change={12} />
              <MetricCard label="Critical"        value={criticalCount} icon="🔴" color="#f97316" change={-5} />
              <MetricCard label="Auto-Mitigated"  value={autoMitigated} icon="🤖" color="#22c55e" />
              <MetricCard label="False Positives" value={fpRate}        icon="🚫" color="#a78bfa" />
            </div>

            {/* Charts Row */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Incident Timeseries */}
              <div className="rounded-2xl border border-white/5 bg-white/3 p-6">
                <h3 className="text-sm text-gray-400 uppercase tracking-wider mb-5">Incidents (24h)</h3>
                <ResponsiveContainer width="100%" height={200}>
                  <AreaChart data={timeseries}>
                    <defs>
                      <linearGradient id="incGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%"  stopColor="#ef4444" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#ef4444" stopOpacity={0}   />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#ffffff08" />
                    <XAxis dataKey="hour" tick={{ fill: "#6b7280", fontSize: 11 }} />
                    <YAxis tick={{ fill: "#6b7280", fontSize: 11 }} />
                    <Tooltip contentStyle={{ background: "#0d0d14", border: "1px solid #ffffff15", borderRadius: 8 }} />
                    <Area type="monotone" dataKey="incidents" stroke="#ef4444" strokeWidth={2} fill="url(#incGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              {/* Activity Timeseries */}
              <div className="rounded-2xl border border-white/5 bg-white/3 p-6">
                <h3 className="text-sm text-gray-400 uppercase tracking-wider mb-5">Group Activity (24h)</h3>
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={timeseries}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#ffffff08" />
                    <XAxis dataKey="hour" tick={{ fill: "#6b7280", fontSize: 11 }} />
                    <YAxis tick={{ fill: "#6b7280", fontSize: 11 }} />
                    <Tooltip contentStyle={{ background: "#0d0d14", border: "1px solid #ffffff15", borderRadius: 8 }} />
                    <Legend wrapperStyle={{ fontSize: 12, color: "#6b7280" }} />
                    <Line type="monotone" dataKey="joins"    stroke="#22c55e" strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="messages" stroke="#3b82f6" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Severity Breakdown */}
            <div className="rounded-2xl border border-white/5 bg-white/3 p-6">
              <h3 className="text-sm text-gray-400 uppercase tracking-wider mb-5">Incident Type Breakdown</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {Object.entries(INCIDENT_TYPE_ICONS).map(([type, icon]) => {
                  const count = incidents.filter(i => i.incident_type === type).length;
                  return (
                    <div key={type} className="flex items-center gap-3 p-3 rounded-xl bg-white/3 border border-white/5">
                      <span className="text-xl">{icon}</span>
                      <div>
                        <div className="text-lg font-bold font-mono text-white">{count}</div>
                        <div className="text-xs text-gray-500 capitalize">{type.replace("_", " ")}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Recent Critical Incidents */}
            <div className="rounded-2xl border border-white/5 bg-white/3 p-6">
              <h3 className="text-sm text-gray-400 uppercase tracking-wider mb-4">Recent Critical Incidents</h3>
              <div className="space-y-2">
                {incidents
                  .filter(i => i.severity >= 3 && i.status === "open")
                  .slice(0, 4)
                  .map(i => (
                    <div
                      key={i.id}
                      onClick={() => setSelectedIncident(i)}
                      className="flex items-center gap-3 p-3 rounded-xl bg-white/3 border border-white/5
                                 hover:border-white/10 cursor-pointer transition-all"
                    >
                      <span className="text-lg">{INCIDENT_TYPE_ICONS[i.incident_type]}</span>
                      <span className="text-sm text-white/80 flex-1 truncate">{i.title}</span>
                      <Badge severity={i.severity} />
                      <span className="text-xs text-gray-500 font-mono shrink-0">
                        {new Date(i.created_at).toLocaleTimeString()}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════════════ */}
        {/* INCIDENTS TAB                                      */}
        {/* ══════════════════════════════════════════════════ */}
        {activeTab === "incidents" && (
          <div className="space-y-5">
            {/* Filters */}
            <div className="flex flex-wrap gap-3 items-center">
              <input
                type="text"
                placeholder="Search incidents…"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className="bg-white/5 border border-white/10 rounded-xl px-4 py-2 text-sm text-white
                           placeholder-gray-600 focus:outline-none focus:border-white/20 w-64"
              />
              <select
                value={filterStatus}
                onChange={e => setFilterStatus(e.target.value)}
                className="bg-white/5 border border-white/10 rounded-xl px-4 py-2 text-sm text-gray-300
                           focus:outline-none focus:border-white/20"
              >
                <option value="all">All Statuses</option>
                {Object.keys(STATUS_CONFIG).map(s => (
                  <option key={s} value={s}>{s.replace("_", " ")}</option>
                ))}
              </select>
              <select
                value={filterSeverity}
                onChange={e => setFilterSeverity(e.target.value)}
                className="bg-white/5 border border-white/10 rounded-xl px-4 py-2 text-sm text-gray-300
                           focus:outline-none focus:border-white/20"
              >
                <option value="all">All Severities</option>
                {Object.entries(SEVERITY_CONFIG).map(([k, v]) => (
                  <option key={k} value={k}>{v.label}</option>
                ))}
              </select>
              <span className="text-sm text-gray-500 ml-auto font-mono">{filteredIncidents.length} results</span>
            </div>

            {/* Table */}
            <div className="rounded-2xl border border-white/5 bg-white/2 overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-white/5 text-xs text-gray-500 uppercase tracking-wider">
                      {["ID", "Incident", "Severity", "Status", "Risk Score", "Mitigated", "Time", "Actions"].map(h => (
                        <th key={h} className="text-left px-4 py-3 font-medium">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredIncidents.map(i => (
                      <IncidentRow
                        key={i.id}
                        incident={i}
                        onAction={handleAction}
                        selected={selectedIncident?.id === i.id}
                        onSelect={setSelectedIncident}
                      />
                    ))}
                  </tbody>
                </table>
                {filteredIncidents.length === 0 && (
                  <div className="text-center py-12 text-gray-600">No incidents match your filters</div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════════════ */}
        {/* GROUPS TAB                                         */}
        {/* ══════════════════════════════════════════════════ */}
        {activeTab === "groups" && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {groups.map(g => (
                <GroupCard
                  key={g.id}
                  group={g}
                  onLockdown={handleLockdown}
                  onRelease={handleRelease}
                />
              ))}
            </div>

            {/* Global Lockdown Banner */}
            {groups.some(g => g.lockdown_active) && (
              <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-5 flex items-center gap-4 animate-fadeIn">
                <span className="text-2xl">🔴</span>
                <div className="flex-1">
                  <div className="font-semibold text-red-400">Active Lockdowns Detected</div>
                  <div className="text-sm text-red-400/70 mt-0.5">
                    {groups.filter(g => g.lockdown_active).map(g => g.title).join(", ")} are currently locked down.
                  </div>
                </div>
                <button
                  onClick={() => groups.filter(g => g.lockdown_active).forEach(g => handleRelease(g.id))}
                  className="px-4 py-2 rounded-xl bg-red-500/20 border border-red-500/40 text-red-400
                             text-sm font-medium hover:bg-red-500/30 transition-all"
                >
                  Release All
                </button>
              </div>
            )}

            {/* Threshold Configurator */}
            <div className="rounded-2xl border border-white/5 bg-white/3 p-6">
              <h3 className="text-sm text-gray-400 uppercase tracking-wider mb-5">Default Thresholds</h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {[
                  { label: "Join Spike Count", key: "join_spike_count", default: 20, unit: "joins" },
                  { label: "Join Spike Window", key: "join_spike_window_s", default: 30, unit: "seconds" },
                  { label: "Message Spike Count", key: "msg_spike_count", default: 100, unit: "messages" },
                  { label: "Link Flood Count", key: "link_flood_count", default: 5, unit: "links" },
                  { label: "New Account Age", key: "new_account_age_days", default: 7, unit: "days" },
                  { label: "Mute Duration", key: "mute_duration_minutes", default: 5, unit: "minutes" },
                ].map(({ label, key, default: def, unit }) => (
                  <div key={key} className="flex items-center gap-3 p-3 rounded-xl bg-white/3 border border-white/5">
                    <div className="flex-1">
                      <div className="text-xs text-gray-500 mb-1">{label}</div>
                      <input
                        type="number"
                        defaultValue={def}
                        className="w-20 bg-white/10 border border-white/10 rounded-lg px-2 py-1 text-sm
                                   text-white focus:outline-none focus:border-blue-500/50"
                      />
                    </div>
                    <span className="text-xs text-gray-600">{unit}</span>
                  </div>
                ))}
              </div>
              <button className="mt-4 px-5 py-2 rounded-xl bg-blue-500/20 border border-blue-500/30
                                 text-blue-400 text-sm hover:bg-blue-500/30 transition-all">
                💾 Save Thresholds
              </button>
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════════════ */}
        {/* LIVE FEED TAB                                      */}
        {/* ══════════════════════════════════════════════════ */}
        {activeTab === "live" && (
          <div className="space-y-5">
            <div className="flex items-center gap-3 mb-2">
              <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-green-500/10 border border-green-500/20">
                <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                <span className="text-sm text-green-400 font-medium">Live Event Stream</span>
              </div>
              <span className="text-xs text-gray-500">{liveFeed.length} events captured</span>
            </div>

            <div className="rounded-2xl border border-white/5 bg-white/2 p-6 max-h-[70vh] overflow-y-auto">
              {liveFeed.length === 0 ? (
                <div className="text-center py-8 text-gray-600">Waiting for events…</div>
              ) : (
                liveFeed.map((event, i) => (
                  <LiveFeedItem key={event.id} event={event} index={i} />
                ))
              )}
            </div>
          </div>
        )}
      </main>

      {/* ── Incident Detail Modal ───────────────────────────── */}
      {selectedIncident && (
        <IncidentDetail
          incident={selectedIncident}
          onAction={handleAction}
          onClose={() => setSelectedIncident(null)}
        />
      )}
    </div>
  );
}
