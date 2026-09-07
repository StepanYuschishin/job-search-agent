# Job Search Agent — V2

> A stateful AI agent for job-search operations with semantic email classification, bounded autonomous actions, WhatsApp human-in-the-loop decision making, persistent state, and scheduled analytics.

![Job Search Agent Architecture](assets/job-search-agent-architecture.png)

---

## Overview

Job Search Agent is a personal agentic AI system built around a simple question:

**What should an AI agent be allowed to do on its own — and when should it ask a human?**

The agent monitors a real Gmail account, interprets recruiting activity, maintains persistent job-search state, performs narrowly defined autonomous actions, and escalates decisions that require judgment to WhatsApp.

The current system supports two execution paths:

```text
CLEAR + SAFE
    ↓
Autonomous action

JUDGMENT REQUIRED
    ↓
WhatsApp HITL
    ↓
Human decision
    ↓
Draft / approval
    ↓
Final safety checks
    ↓
Execution
```

---

## Architecture

![Job Search Agent Architecture](assets/job-search-agent-architecture.png)

At a high level:

```text
Gmail
  ↓
Discovery + Retrieval
  ↓
AI Classification
  ↓
Decision Layer
  ↓
Guardrails
  ├── Safe deterministic action → Autonomous execution
  │
  └── Judgment required → WhatsApp HITL
                            ↓
                       Human decision
                            ↓
                      Draft generation
                            ↓
                         Approval
                            ↓
                    Final safety checks
                            ↓
                           Gmail
```

The system deliberately separates:

**probabilistic reasoning → decision policy → human judgment → deterministic execution safety**

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the detailed architecture.

---

## What V2 Adds

V2 introduces a human-in-the-loop control layer through WhatsApp.

The agent can now:

- detect recruiter interactions requiring judgment;
- route ambiguous rejection cases to human review;
- recommend an action;
- create persistent HITL actions;
- notify the user through WhatsApp;
- generate recruiter-response drafts on request;
- wait for explicit approval before sending;
- accept edit or ignore decisions;
- preserve HITL state across separate runtime executions;
- apply deterministic safety checks even after human approval.

The result is not unrestricted autonomy.

It is **bounded autonomy with escalation**.

---

## Agent Flow

A normal scheduled run follows this sequence:

1. **Trigger** — macOS `launchd` starts the agent.
2. **Discover** — Gmail is searched for relevant recruiting activity.
3. **Retrieve** — existing classifications are reused from persistent state.
4. **Classify** — new messages are semantically classified by an LLM.
5. **Decide** — the system determines whether the case is autonomous, irrelevant, or requires human judgment.
6. **Guard** — deterministic rules constrain external actions.
7. **Act or escalate**:
   - safe predefined actions may execute autonomously;
   - recruiter interactions and ambiguous rejection cases can enter HITL.
8. **Persist** — classifications, replies, and HITL actions are stored locally.
9. **Process human decisions** — requested drafts and approved sends are handled.
10. **Analyze** — job-search metrics are calculated.
11. **Report** — the dashboard is delivered by email.

---

## AI Classification

Relevant emails are classified into five hiring states:

- `APPLICATION_CONFIRMATION`
- `REJECTION`
- `INTERVIEW`
- `RECRUITER_REPLY`
- `OTHER`

Each classification includes:

- label;
- confidence;
- semantic reason.

The classifier evaluates meaning rather than relying only on exact keyword matching.

This allows the system to interpret recruiting language across different companies, ATS platforms, and writing styles.

---

## Decision Layer

Classification answers:

**What happened?**

The decision layer answers:

**What should happen next?**

For recruiter-related interactions, the decision router can recommend:

- `DRAFT`
- `REVIEW`
- `IGNORE`
- `AUTO`

A recommendation is not execution authority.

For example, an ambiguous rejection may be classified as `REJECTION` but fail the confidence or semantic-evidence requirements for autonomous handling.

Instead of silently acting or discarding the case, V2 can route it to HITL.

---

## WhatsApp Human-in-the-Loop

When human judgment is required, the agent creates a persistent action and sends a WhatsApp notification containing the relevant context and its recommendation.

The interaction is intentionally small.

### Before a draft exists

```text
DRAFT  → generate a reply
IGNORE → close the action
```

### After a draft is generated

```text
SEND   → approve the draft for sending
EDIT   → request changes
IGNORE → close the action
```

`HELP` returns the available commands.

Unknown commands are handled safely without modifying action state.

WhatsApp is therefore a lightweight control surface rather than a second application UI.

---

## HITL State Machine

Human decisions may happen minutes or hours after the original email was discovered, so HITL cannot depend on process memory.

Actions are persisted in:

```text
state/pending-actions.json
```

The lifecycle includes states such as:

```text
PENDING_HUMAN
      ↓ DRAFT
DRAFT_REQUESTED
      ↓
AWAITING_APPROVAL
   ├── SEND
   ├── EDIT
   └── IGNORE
```

This allows WhatsApp input, scheduled agent runs, draft generation, and Gmail execution to happen in separate processes without losing context.

---

## Bounded Autonomy

The agent intentionally does **not** have unrestricted authority.

A rejection reply may execute autonomously only when all required guardrails pass, including:

- classification is `REJECTION`;
- confidence meets the configured threshold;
- semantic reasoning contains rejection evidence;
- sender is replyable;
- destination is not blocked;
- subject is not blocked;
- message is not self-sent;
- message has not already been handled;
- Gmail thread has not already been handled;
- configured batch limits are respected.

If a rejection is ambiguous, it is not promoted into autonomous execution simply because the classifier happened to call it a rejection.

It can instead be escalated to HITL.

---

## Human Approval Is Not a Safety Bypass

V2 deliberately separates **approval** from **permission to execute**.

A human `SEND` command means:

**I approve this draft for sending.**

It does not mean:

**Ignore every safety constraint and send it anyway.**

Final deterministic checks still apply before external execution.

This was verified during end-to-end testing when a controlled self-send reached the final send stage and was correctly blocked by the self-sender guardrail.

The general model is:

```text
LLM recommendation
        ↓
Human approval
        ↓
Deterministic execution checks
        ↓
External action
```

---

## Autonomous vs Human-Controlled Actions

### The agent may autonomously

- retrieve job-search emails;
- classify recruiting activity;
- maintain persistent state;
- calculate job-search metrics;
- identify safe rejection replies;
- send a predefined rejection response when every required guardrail passes;
- deliver scheduled dashboards.

### Human approval is required for

- generated recruiter replies;
- ambiguous recruiter interactions;
- cases routed into HITL;
- free-form generated communication.

### Outside the current autonomy boundary

The agent does not autonomously:

- negotiate salary or employment terms;
- accept or reject offers;
- schedule interviews;
- modify job applications;
- apply for jobs;
- bypass deterministic safety rules.

---

## Persistent State

The system currently maintains three primary local state stores.

### Classification Cache

```text
state/job-search-classifications.json
```

Stores semantic classifications so historical Gmail messages do not need to be repeatedly fetched and reclassified.

### Rejection Reply Ledger

```text
state/rejection-replies.json
```

Tracks handled message and thread IDs to provide idempotency and duplicate protection.

### HITL Action State

```text
state/pending-actions.json
```

Tracks human-review actions across discovery, drafting, approval, execution, and closure.

All runtime state is excluded from Git.

---

## Reliability Engineering

The initial implementation repeatedly processed historical Gmail data.

A run could repeat:

- Gmail searches;
- full-message retrieval;
- semantic classification;
- historical metric calculation.

Observed runtime was approximately:

**10–30 minutes**

The architecture was changed to persist classification metadata and reuse previous AI results.

Observed cached execution:

**~9 seconds**

Compared with a typical 10-minute run, this represents approximately a **60× runtime improvement**.

The important architectural change was moving from repeated historical computation to **stateful incremental processing**.

---

## Safety and Idempotency

The system uses several independent controls rather than relying on a single LLM decision:

- classification confidence thresholds;
- semantic rejection evidence;
- blocked sender checks;
- blocked subject checks;
- self-sender protection;
- message-level duplicate protection;
- thread-level duplicate protection;
- persistent HITL action state;
- bounded batch execution;
- explicit human approval for generated replies;
- final deterministic checks before send.

This creates defense in depth around external communication.

---

## Scheduling

The production agent runs through macOS `launchd`.

Current schedule:

```text
09:00
18:00
```

The scheduler executes:

```text
.venv/bin/python src/run.py
```

Manual execution uses the same runtime:

```bash
.venv/bin/python src/run.py
```

---

## Technology

- **Python** — orchestration, state management, and business logic
- **OpenAI API** — semantic classification, decision support, and draft generation
- **Gmail API** — email retrieval and bounded send actions
- **WhatsApp Cloud API** — human-in-the-loop notifications and commands
- **Meta Webhooks** — inbound WhatsApp command delivery
- **OAuth 2.0** — Gmail authorization
- **JSON** — persistent local runtime state
- **macOS launchd** — scheduled execution
- **Git / GitHub** — source control and technical documentation

---

## Repository Structure

```text
job-search-agent/
├── README.md
├── .env.example
├── .gitignore
├── LICENSE
├── requirements.txt
│
├── src/
│   ├── run.py
│   ├── job_search.py
│   ├── decision_router.py
│   ├── draft_generator.py
│   ├── hitl.py
│   ├── webhook.py
│   └── whatsapp.py
│
├── docs/
│   ├── ARCHITECTURE.md
│   └── PORTFOLIO.md
│
├── assets/
│   └── job-search-agent-architecture.png
│
├── deployment/
│   └── com.stepan.job-search-agent.plist
│
└── scripts/
    ├── install.sh
    └── uninstall.sh
```

Local credentials, OAuth tokens, runtime state, caches, and logs are intentionally excluded from the repository.

---

## Configuration

Create the local configuration:

```bash
cp .env.example .env
```

Core configuration includes:

```env
# OpenAI
OPENAI_API_KEY=your_openai_api_key_here
JOB_SEARCH_AGENT_CLASSIFIER_MODEL=gpt-4.1-mini
JOB_SEARCH_AGENT_DRAFT_MODEL=gpt-4.1-mini

# Job Search Agent
JOB_SEARCH_AGENT_SELF_EMAIL=your_email@example.com
JOB_SEARCH_AGENT_DASHBOARD_RECIPIENT=your_email@example.com
JOB_SEARCH_AGENT_START_DATE=2026-06-22
JOB_SEARCH_AGENT_REJECTION_CONFIDENCE=0.95
JOB_SEARCH_AGENT_MAX_REPLY_BATCH=20
JOB_SEARCH_AGENT_MAX_RUN_SECONDS=600
JOB_SEARCH_AGENT_HITL_LOOKBACK_DAYS=7
JOB_SEARCH_AGENT_MAX_HITL_BATCH=20

# WhatsApp HITL
WHATSAPP_ACCESS_TOKEN=your_whatsapp_system_user_token_here
WHATSAPP_PHONE_NUMBER_ID=your_whatsapp_phone_number_id_here
WHATSAPP_RECIPIENT_NUMBER=your_whatsapp_recipient_number_here
WHATSAPP_API_VERSION=v26.0
WHATSAPP_VERIFY_TOKEN=choose_your_webhook_verify_token
WEBHOOK_HOST=127.0.0.1
WEBHOOK_PORT=8080
```

Never commit `.env`, OAuth credentials, tokens, runtime state, or logs.

---

## Running Locally

Create and activate the environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Configure `.env`, add your Google OAuth credentials as:

```text
credentials.json
```

Then run:

```bash
python src/run.py
```

The first Gmail authorization creates a local:

```text
token.json
```

Both OAuth files are excluded from Git.

---

## Installation

For the complete macOS setup:

```bash
bash scripts/install.sh
```

The installer handles the Python environment, dependencies, Gmail authentication, scheduler installation, and an initial smoke test.

To remove automatic execution without deleting local state:

```bash
bash scripts/uninstall.sh
```

---

## Current Product Scope

V2 currently implements:

```text
Gmail
  ↓
Semantic hiring-state classification
  ↓
Persistent state
  ↓
Decision routing
  ↓
┌─────────────────────┬─────────────────────┐
│ bounded autonomous  │ WhatsApp HITL       │
│ execution           │                     │
└─────────────────────┴─────────────────────┘
                       ↓
                 Draft + approval
                       ↓
                Safety guardrails
                       ↓
                    Gmail
```

The product has evolved from an email analytics script into a small stateful agent with two explicit control modes:

**autonomy when the action is constrained enough, human judgment when it is not.**

That boundary is intentionally part of the product architecture rather than left to the language model.

---

## Production Status

V2 has been exercised end-to-end across:

```text
Gmail
→ classification
→ HITL discovery
→ persistent action
→ WhatsApp notification
→ human DRAFT command
→ draft generation
→ WhatsApp approval request
→ human SEND command
→ final execution guardrail
```

Outbound WhatsApp delivery and inbound Meta webhook handling are operational.

The scheduler runs the main agent at **09:00** and **18:00**.

---

## Future Extensions

Possible extensions include:

- richer recruiter-response recommendations;
- improved `EDIT` interaction;
- structured opportunity tracking;
- application-source integrations;
- interview scheduling workflows;
- funnel analytics;
- additional human-approved actions;
- richer WhatsApp interaction patterns.

The autonomy boundary should expand only when a new action has an explicit policy, clear failure modes, and appropriate execution controls.