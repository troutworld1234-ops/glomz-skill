# Glomz — Privacy Policy

**Last updated:** January 2025  
**Contact:** privacy@glomz.com  
**App:** Glomz — Scene Radar (glomz.com)

---

## 1. Who We Are

Glomz is a privacy-first local discovery app. We built it with the same rigor that CISA applies to critical infrastructure — **least-privilege data access, zero-trust by default, and data minimization at every layer**.

This is a personal/federal-employee-after-hours project. It is not affiliated with any government agency or employer. The privacy commitments below are independent, self-imposed, and verifiable.

---

## 2. Our Core Privacy Principles

### 2.1 Session-Based Only
All user data is **volatile and session-scoped**. When you close the app, refresh the page, or your browser session ends — **your data is automatically and completely purged**. No cookies store identity. No fingerprinting tracks you across visits. No database holds your information.

### 2.2 Pseudonymous by Design
You choose a handle (pseudonym). It is:
- **Not linked** to your real name, email, phone, or device ID
- **Regenerated** or changeable every session
- **Never persisted** to any server or database
- **Never sold or shared** with third parties

### 2.3 Home Zone Geofence
You can draw a circle on the map as your "Home Zone." When your GPS indicates you are inside this circle:
- Your location is **never transmitted** to our servers
- Your location is **never shown** to other users
- The app displays this status locally and **redacts** before any outbound data

### 2.4 Aggregate-Only Heatmaps
Heatmap data showing activity density is:
- **Aggregated across at least 5 data points** before display
- **Never based on individual coordinates**
- **Binned into grid cells** before visualization
- **Never traceable** back to a specific person

### 2.5 Minimum Thresholds for Matching
When you use the "Glom" feature to find nearby users with similar interests:
- Results are only shown if **≥ 3 anonymous users** match within the area
- No individual is identifiable — all results are pseudonymous group displays
- If fewer than 3 matches exist, the feature shows "No matches" rather than expose small-group data

### 2.6 Foreground-Only Location Access
Location is accessed using the "while using" pattern:
- GPS is queried **only when the app tab is visible/active**
- Location tracking **stops automatically** when you switch tabs or minimize
- Background geolocation is **never requested or used**
- Location accuracy is set to **standard** (not high-accuracy) to conserve battery

---

## 3. What Data We Collect (and Don't)

### 3.1 Data We DO Handle (Volatile, Session-Only)

| Category | What | Stored? | Shared? |
|---|---|---|---|
| Pseudonym | Your chosen handle | Session memory only | Shown to others as anonymous tag |
| Interests | Tags you select | Session memory only | Used for anonymous matching |
| Location | Your GPS coordinates | Session memory only, or null if in home zone | Used locally for nearby filtering |
| Home Zone | Your drawn circle bounds | Session memory only | Never transmitted |

### 3.2 Data We DO NOT Collect

- ❌ **Email addresses** (no account required)
- ❌ **Phone numbers**
- ❌ **Real names**
- ❌ **Device fingerprints** (canvas, WebGL, fonts, navigator properties)
- ❌ **IP addresses** (not logged or stored)
- ❌ **Behavioral analytics** (no Mixpanel, Google Analytics, Amplitude, etc.)
- ❌ **Advertising IDs** (IDFA, GAID)
- ❌ **Cookies** beyond the minimal service worker cache for offline loading
- ❌ **Cross-site tracking**
- ❌ **Biometric data**
- ❌ **Contact lists** or social graph data
- ❌ **Messages or chat history**

---

## 4. Data Flow

```
Your Device                    Glomz
┌──────────────────┐          ┌──────────────────────┐
│  GPS Location    │───┐      │  Session Memory       │
│  Interests       │   │      │  (volatile, RAM only) │
│  Home Zone       │   └─────▶│                       │
│  Pseudonym       │          │  Never persisted      │
└──────────────────┘          └──────────────────────┘
         │                              │
         ▼                              ▼
  Local processing              Mock/static data
  (redaction, aggregation)      returned to your browser
```

**No user data traverses the network.** The MVP serves mock/static data. Your personal choices (handle, interests, home zone) remain entirely in your browser's memory and are destroyed on page unload.

---

## 5. Technology Stack & Security

### 5.1 Client-Side
- **Service Worker:** Caches only app shell (HTML, CSS, JS) — no user data
- **No Analytics SDKs:** Zero tracking scripts embedded
- **CSP-ready:** The app is designed to work with strict Content Security Policies
- **HTTPS required** in production deployment

### 5.2 Server-Side (Phase 2 / Future)
- **PostGIS** for spatial queries — data encrypted at rest (when persistence is added)
- **Session-scoped** tokens — no long-lived authentication
- **Rate limiting** to prevent scraping of deal/location data
- **Audit logging** of data access requests (if/when user data is persisted)

### 5.3 Third-Party Dependencies
Our app loads the following CDN resources:
- **Leaflet.js** — Open-source map library (ISC License)
- **CartoDB/CARTO tiles** — Map tile imagery (OSM data, CC-BY)
- **Leaflet.heat** — Heatmap plugin (public)

No analytics, advertising, or tracking libraries are included. CDN resources are loaded over HTTPS. These third parties may log standard web server access logs per their own policies, but we do not send personal data to them.

---

## 6. Geographic Scope — Birmingham First

Glomz launches in the **Birmingham, Alabama metro area** (approximately 33.4°N–33.7°N, -87.0°W–-86.6°W).

- Map data and deals are **geo-fenced** to this bounding box for MVP
- Location data outside this region is **clamped** to the nearest boundary point
- The app displays all Birmingham-area activity; no global tracking

---

## 7. Your Rights

Because we don't store your data, you don't need to exercise traditional data rights:

- **Access:** All your data is visible in your browser session
- **Deletion:** Close the tab, refresh the page, or click "Purge Session Data" in the menu
- **Export:** Not applicable — nothing is persisted
- **Correction:** Change your selections at any time; old data is immediately replaced
- **Opt-out:** Don't open the app

We comply with:
- **No purpose** to be a controller under GDPR (no data collection)
- **CCPA:** No selling of personal information (we collect none)
- **COPPA:** Not directed to children under 13

---

## 8. Data Retention

**Zero.** By design:

| Data Type | Retention |
|---|---|
| User session data | Until page unload/refresh/close |
| Server access logs | Standard rotation per hosting provider |
| Mock deals/static data | As posted (public information) |
| Business dashboard data | Future: 90-day rolling maximum |

---

## 9. Children's Privacy

Glomz is not directed to children under 13 and does not knowingly collect information from children. If we discover a minor is using the app before persistence is added, there is no data to delete. When persistence is added in Phase 3, we will implement age verification and COPPA compliance procedures.

---

## 10. Changes to This Policy

We will:
- Update the "Last updated" date when material changes are made
- **Never retroactively apply** more intrusive data practices without explicit consent
- Post a prominent notice for any changes that expand data collection

---

## 11. Contact & Questions

- **Email:** privacy@glomz.com
- **Website:** glomz.com
- **Source code:** [GitHub](https://github.com/glomz-app) (open-source, auditable)

---

## 12. Federal Employee Note

This project was developed entirely outside of official hours and duties, without government resources, and does not involve classified or sensitive government information. It is a personal side project and is not endorsed by, affiliated with, or representative of any federal agency employing the developer. Privacy practices described here are self-imposed standards, not government policy.

---

*Built with CISA-grade privacy. Your data, your control, your session.*
