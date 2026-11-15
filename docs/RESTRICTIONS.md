# Restrictions

Each user-facing refusal in the code cites one of these (`// restriction: <ID>`).

| ID | Message | Why | Approved |
| --- | --- | --- | --- |
| SR-ONLINE-01 | `inject_refused online_mode` / `online_deluxe` / `no_scene_report` / `scene_report_stale` / `nro_guards_failed` | Remote input must never reach an internet (NSO) online scene (ban risk); local wireless is allowed (operator correction 2026-11-15 ~23:14Z); fail closed when the report is missing, stale, unguarded or ambiguous | brief 2026-11-15 21:19Z (Safety); operator ruling 2026-11-15 ("fail CLOSED on input injection unless the in-game NRO confirms an offline scene") |
| SR-SYSBTN-01 | `system button` (HOME/Capture refused from the host) | The host may not press HOME or Capture; only the fixed LAUNCH/DISMISS sequences press HOME/A, and only without an application | brief 2026-11-15 (Safety, derived); confirmation requested in the INBOX 2026-11-15 21:46Z |
| SR-LAUNCH-01 | `refused application_running` | LAUNCH/DISMISS press buttons without a scene report, so they run only while no application process exists | primary ruling A 2026-11-15 (relayed from the operator: LAUNCH/DISMISS only without an application; LAUNCH only after the first-tile check) |
| SR-PEER-01 | peer closed (`not_private`) | LAN only: the brief requires private peers | brief 2026-11-15 (Network) |
