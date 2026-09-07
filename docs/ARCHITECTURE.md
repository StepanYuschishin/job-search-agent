# Job Search Agent — V2 Architecture

## 1. High-Level Architecture

```text
Schedule / Manual Trigger
        ↓
macOS launchd
        ↓
Run Orchestrator
        ↓
Gmail Discovery + Retrieval
        ↓
Semantic Classification
        ↓
Persistent State
        ↓
Decision Layer
        ↓
Deterministic Guardrails
        ↓
┌────────────────────────────┬──────────────────────────────┐
│ SAFE / CONSTRAINED         │ JUDGMENT REQUIRED            │
│                            │                              │
│ Autonomous action          │ WhatsApp HITL                │
│                            │      ↓                       │
│ Predefined rejection reply │ Human decision               │
│ Dashboard delivery         │      ↓                       │
│                            │ Draft generation             │
│                            │      ↓                       │
│                            │ Human approval               │
│                            │      ↓                       │
│                            │ Final safety checks          │
└────────────────────────────┴──────────────┬───────────────┘
                                           ↓
                                         Gmail
```

The core architectural principle is:

**probabilistic reasoning does not equal execution authority.**

The LLM interprets and recommends.
Application policy decides what path is allowed.
Humans handle judgment-sensitive actions.
Deterministic code controls final execution.

---

## 2. Core Components

### Scheduler

macOS `launchd`

Production schedule:

```text
09:00
18:00
```

The same runtime can also be started manually:

```bash
.venv/bin/python src/run.py
```

---

### Run Orchestrator

`src/run.py`

Coordinates one complete agent execution:

1. run safe rejection automation;
2. discover recruiter / ambiguous-rejection HITL candidates;
3. process requested drafts;
4. process approved sends;
5. calculate analytics;
6. deliver dashboard;
7. enforce maximum runtime boundaries.

The orchestrator sequences capabilities but does not own the underlying Gmail, HITL, drafting, or decision logic.

---

### Core Job Search Logic

`src/job_search.py`

Contains:

- Gmail authentication;
- candidate email discovery;
- retrieval and parsing;
- semantic classification;
- classification cache;
- rejection automation;
- rejection guardrails;
- recruiter HITL discovery;
- ambiguous rejection routing;
- duplicate protection;
- Gmail execution;
- analytics;
- dashboard delivery.

---

### Decision Router

`src/decision_router.py`

The decision router evaluates recruiter-related interactions after classification.

Supported recommendations:

```text
DRAFT
REVIEW
IGNORE
AUTO
```

A recommendation does not itself authorize an external action.

For ambiguous rejection cases, `AUTO` is explicitly downgraded to `REVIEW`.

---

### Draft Generator

`src/draft_generator.py`

Generates human-reviewed recruiter replies.

Draft rules include:

- do not invent facts;
- do not invent commitments or availability;
- do not use placeholders such as `[Candidate Name]`;
- use `Stepan` as sign-off when appropriate;
- never send automatically.

Generated drafts enter an explicit approval state before Gmail execution.

---

### HITL State Manager

`src/hitl.py`

Maintains persistent human-review actions across separate processes and scheduled executions.

Primary state file:

```text
state/pending-actions.json
```

The HITL layer ensures that a WhatsApp decision made later can still be associated with the correct Gmail message, thread, recommendation, and draft.

---

### WhatsApp Integration

`src/whatsapp.py`

Uses the WhatsApp Cloud API for outbound interaction.

Responsibilities include:

- sending HITL notifications;
- sending generated drafts for approval;
- sending command/help responses.

WhatsApp acts as the lightweight control surface for the agent.

---

### Webhook

`src/webhook.py`

Receives inbound WhatsApp messages through Meta Webhooks.

Supported commands:

```text
DRAFT
SEND
EDIT
IGNORE
HELP
```

Unknown commands do not modify state and return the supported command list.

---

## 3. Semantic Classification

Relevant Gmail messages are classified into:

```text
APPLICATION_CONFIRMATION
REJECTION
INTERVIEW
RECRUITER_REPLY
OTHER
```

Each result includes:

- label;
- confidence;
- semantic reason.

The classifier answers:

**What happened in this email?**

It does not decide whether an external action is permitted.

---

## 4. Persistent State

Runtime state lives under:

```text
state/
```

and is excluded from Git.

### Classification Cache

```text
state/job-search-classifications.json
```

Stores processed message metadata and classification results.

Purpose:

- avoid repeated Gmail retrieval;
- avoid repeated LLM classification;
- support incremental processing.

### Rejection Reply Ledger

```text
state/rejection-replies.json
```

Stores handled Gmail message and thread IDs.

Purpose:

- message-level idempotency;
- thread-level idempotency;
- duplicate-send prevention.

### HITL Action State

```text
state/pending-actions.json
```

Stores human-review actions across their lifecycle.

Purpose:

- preserve context between WhatsApp input and later scheduled runs;
- connect Gmail messages to recommendations, drafts, approvals, and outcomes.

---

## 5. Autonomous Rejection Path

A message classified as `REJECTION` is not automatically replyable.

It must pass the full guardrail chain.

```text
REJECTION
    ↓
confidence >= configured threshold
    ↓
semantic rejection evidence exists
    ↓
sender / reply-to is allowed
    ↓
subject is allowed
    ↓
not self-sent
    ↓
message not already handled
    ↓
thread not already handled
    ↓
within configured batch bound
    ↓
SEND PREDEFINED REPLY
```

If a required safety condition fails, the autonomous send path is blocked.

Some failures represent an unsafe destination and are simply skipped.

Others represent ambiguity and may enter HITL instead.

---

## 6. Ambiguous Rejection Routing

V2 distinguishes between:

```text
SAFE REJECTION
→ deterministic autonomous path
```

and:

```text
AMBIGUOUS REJECTION
→ human review
```

A rejection can be considered ambiguous when:

- classification confidence is below the autonomous threshold; or
- semantic rejection evidence is insufficient.

If safety constraints allow human review, the case can enter the same HITL path used for recruiter interactions.

This prevents uncertainty from being silently converted into autonomous communication.

---

## 7. Recruiter HITL Flow

Recruiter interactions above the HITL confidence threshold can enter human review.

```text
RECRUITER_REPLY
       ↓
Safety filtering
       ↓
Decision router
       ↓
Recommended action
       ↓
Create persistent HITL action
       ↓
WhatsApp notification
       ↓
PENDING_HUMAN
```

The WhatsApp notification contains:

- sender;
- subject;
- classification;
- classification confidence;
- classification reason;
- recommended action;
- decision confidence;
- decision reason;
- action ID.

---

## 8. HITL State Machine

### Initial review

```text
PENDING_HUMAN
   ├── DRAFT
   │     ↓
   │ DRAFT_REQUESTED
   │     ↓
   │ Draft generation
   │     ↓
   │ AWAITING_APPROVAL
   │
   └── IGNORE
         ↓
      IGNORED
```

### Draft approval

```text
AWAITING_APPROVAL
   ├── SEND
   │     ↓
   │ SEND_APPROVED
   │     ↓
   │ Final execution checks
   │     ↓
   │ Gmail send / blocked result
   │
   ├── EDIT
   │     ↓
   │ Revision path
   │
   └── IGNORE
         ↓
      IGNORED
```

Human interaction and execution can therefore occur in completely separate processes.

---

## 9. Human Approval Is Not Execution Authority

A `SEND` command means:

**The human approves this draft.**

It does not mean:

**Bypass all safety rules.**

Before Gmail execution, deterministic checks still apply.

This architecture was validated in a controlled end-to-end self-send test:

```text
Gmail message
→ HITL
→ DRAFT
→ draft generation
→ AWAITING_APPROVAL
→ SEND
→ SEND_APPROVED
→ final safety layer
→ BLOCKED by self-sender guardrail
```

The pipeline therefore reached the execution boundary successfully and stopped for the intended safety reason.

---

## 10. WhatsApp Interaction Model

WhatsApp is intentionally command-based rather than conversationally unrestricted.

### Pending human decision

```text
DRAFT
IGNORE
```

### Draft awaiting approval

```text
SEND
EDIT
IGNORE
```

### Utility

```text
HELP
```

Unknown commands are handled safely.

Current commands operate on the latest relevant open action rather than requiring the user to manually provide an action ID.

---

## 11. Duplicate and Safety Protection

The system uses multiple independent controls:

- classification confidence;
- semantic evidence;
- blocked sender checks;
- blocked subject checks;
- self-sender guardrail;
- message-level reply ledger;
- thread-level reply ledger;
- open HITL-action detection;
- explicit human approval;
- final deterministic execution checks;
- bounded batch execution.

The design assumes that no individual layer is sufficient by itself.

---

## 12. Analytics Pipeline

The classified message snapshot is reused for job-search metrics.

This avoids separate Gmail and LLM passes for each reporting window.

The dashboard can include:

- cumulative applications;
- cumulative rejections;
- recent activity;
- rejection reply metrics;
- runtime information.

Dashboard delivery is itself a narrowly permitted autonomous write action.

---

## 13. Reliability Evolution

The original implementation repeatedly processed historical Gmail data.

Observed runtime:

```text
10–30 minutes
```

V2 reuses persistent classification state and performs incremental processing.

Observed cached runtime:

```text
~9 seconds
```

Compared with a representative 10-minute run, this is approximately a:

```text
60× runtime improvement
```

The architectural improvement came primarily from changing:

```text
stateless historical recomputation
```

into:

```text
stateful incremental processing
```

---

## 14. Security Boundary

The repository intentionally excludes:

```text
.env
credentials.json
token.json
state/
*.log
.venv/
```

Secrets and OAuth tokens remain local.

WhatsApp, Gmail, and OpenAI credentials are provided through local environment configuration.

The system never relies on GitHub as a runtime secret store.

---

## 15. Current Control Model

The system now has three distinct levels of authority.

### Level 1 — Observe

The agent may independently:

- retrieve;
- classify;
- analyze;
- maintain state.

### Level 2 — Bounded Autonomy

The agent may independently execute actions only when an explicit policy and deterministic guardrails permit them.

Current examples:

- predefined safe rejection reply;
- dashboard delivery.

### Level 3 — Human-in-the-Loop

Higher-judgment communication requires human involvement.

Current examples:

- recruiter interactions;
- ambiguous rejection cases;
- generated recruiter replies.

The autonomy boundary is therefore a first-class architectural component rather than an implicit LLM decision.

---

## 16. V2 End-to-End Flow

```text
macOS launchd
      ↓
src/run.py
      ↓
Gmail
      ↓
Discovery / Retrieval
      ↓
Classification
      ↓
Persistent State
      ↓
Decision
      ↓
       safe enough?
      /            \
    YES            NO / JUDGMENT
     ↓                  ↓
Bounded action       WhatsApp
     ↓                  ↓
Guardrails          Recommendation
     ↓                  ↓
Execution          Human decision
                        ↓
                      Draft
                        ↓
                     Approval
                        ↓
                 Final guardrails
                        ↓
                      Gmail
```

This is the central V2 product model:

**the agent acts alone when the action is constrained enough, and asks when judgment matters.**
