# Job Search Agent — Architecture

## 1. High-Level Architecture

```text
Schedule / Manual Trigger
        ↓
macOS launchd
        ↓
Run Orchestrator
        ↓
Gmail Retrieval
        ↓
Semantic Classification
        ↓
Persistent Local State
        ↓
Decision & Guardrail Layer
        ↓
┌─────────────────────────────┬─────────────────────────────┐
│ Analytics Path              │ Rejection Automation Path   │
│                             │                             │
│ Aggregate metrics           │ Detect rejection            │
│ Generate dashboard          │ Evaluate replyability       │
│ Send dashboard email        │ Enforce guardrails          │
│                             │ Prevent duplicate replies   │
│                             │ Send bounded reply          │
└─────────────────────────────┴─────────────────────────────┘
```

## 2. Core Components

### Scheduler

macOS `launchd` triggers the agent automatically on schedule.

The scheduler is configured separately from the application logic so the agent can also be executed manually for testing and debugging.

### Run Orchestrator

`src/run.py`

Coordinates one complete agent execution:

1. discover replyable rejection emails;
2. send permitted predefined replies;
3. generate job-search analytics;
4. email the dashboard;
5. terminate if the run exceeds the configured maximum runtime.

The orchestrator does not contain the core Gmail or classification logic. Its responsibility is execution sequencing and runtime control.

### Core Agent Logic

`src/job_search.py`

Contains the main job-search system capabilities:

- Gmail authentication;
- candidate email discovery;
- message retrieval and parsing;
- semantic classification;
- persistent state management;
- rejection decision logic;
- duplicate protection;
- bounded email actions;
- dashboard calculation;
- dashboard delivery.

### Gmail Integration

The system uses the Gmail API with OAuth 2.0 authorization.

Gmail is the primary external environment from which the agent:

- discovers recruiting emails;
- retrieves message metadata and content;
- identifies conversation threads;
- sends permitted predefined rejection replies;
- sends its own analytics dashboard.

Write authority is deliberately constrained by application-level guardrails.

### Semantic Classification

New candidate messages are classified with an LLM into one of five hiring states:

- `APPLICATION_CONFIRMATION`
- `REJECTION`
- `INTERVIEW`
- `RECRUITER_REPLY`
- `OTHER`

Each classification contains:

- label;
- confidence;
- semantic reason.

The classifier evaluates the meaning of the message rather than depending only on exact keyword matches.

### Persistent Local State

Runtime state is stored locally under:

```text
state/
```

The state directory is created automatically and excluded from source control.

#### Classification Cache

`state/job-search-classifications.json`

Stores previously processed email metadata and classification results, including:

- classification;
- confidence;
- semantic reason;
- thread ID;
- date;
- subject;
- sender;
- reply-to;
- snippet.

Purpose:

Avoid repeatedly retrieving full historical messages and paying for repeated LLM classification.

#### Rejection Reply Ledger

`state/rejection-replies.json`

Stores successfully handled rejection messages and Gmail thread IDs.

Purpose:

Provide idempotency and prevent duplicate autonomous replies.

## 3. Agent Flow

### Step 1 — Trigger

The system starts through either:

- macOS `launchd`; or
- manual execution of `src/run.py`.

Scheduled execution does not require Cursor or Terminal to remain open.

### Step 2 — Discover Candidate Emails

The agent searches Gmail for a broad candidate set using job-search-related concepts such as:

- application;
- candidate;
- recruiter;
- interview;
- hiring;
- position;
- role;
- rejection-related language.

Results from multiple searches are deduplicated by Gmail message ID.

### Step 3 — Retrieve or Reuse State

For each candidate message:

```text
Classification + metadata already cached?
        │
   ┌────┴────┐
  YES        NO
   │          │
Reuse      Retrieve message
state      from Gmail
              ↓
           Extract content
              ↓
           Classify with LLM
              ↓
           Persist result
```

This makes historical processing incremental rather than repeatedly recomputing the entire mailbox state.

### Step 4 — Semantic Classification

The LLM determines the most specific hiring state represented by each message.

For example:

```text
"Thank you for applying, but we decided to move forward
with another candidate."
```

is classified as:

```text
REJECTION
```

rather than:

```text
APPLICATION_CONFIRMATION
```

because the final hiring state takes precedence over generic acknowledgement language.

### Step 5 — Rejection Decision

Emails classified as `REJECTION` are not automatically answered merely because of their label.

They must pass additional deterministic guardrails.

The decision path is approximately:

```text
REJECTION
    ↓
confidence >= threshold
    ↓
explicit rejection evidence
    ↓
replyable sender
    ↓
safe subject
    ↓
not self-sent
    ↓
message not previously answered
    ↓
thread not previously answered
    ↓
within batch safety bound
    ↓
SEND PREDEFINED REPLY
```

If any required condition fails:

```text
DO NOT SEND
```

The LLM therefore contributes semantic judgment, while deterministic code controls whether an external action is permitted.

## 4. Duplicate Protection

Autonomous replies are protected at two levels:

### Message-level protection

A Gmail message ID that has already been handled cannot trigger another reply.

### Thread-level protection

A different message inside an already-handled Gmail conversation cannot trigger another autonomous reply.

Together these protections make the write operation effectively idempotent across repeated scheduled executions.

## 5. Analytics Pipeline

The classified message snapshot is reused to calculate multiple job-search windows:

- cumulative applications;
- cumulative rejections;
- yesterday's activity;
- last seven days of activity.

The system does not perform a separate historical Gmail + LLM pass for every dashboard metric.

This reduces:

- API calls;
- LLM calls;
- latency;
- cost;
- failure surface.

## 6. Dashboard Delivery

Each scheduled run can generate and email a dashboard containing:

- cumulative application count;
- cumulative rejection count;
- yesterday's activity;
- last seven days of activity;
- autonomous actions performed during the run;
- system execution information.

Dashboard delivery is itself a narrowly defined write action: the system may send its own predefined operational report, but this does not grant general-purpose email authority.

## 7. Autonomy Boundary

The system intentionally has bounded autonomy.

### The agent may autonomously

- retrieve job-search emails;
- classify recruiting messages;
- maintain persistent state;
- calculate analytics;
- identify safe rejection responses;
- send predefined rejection replies when all guardrails pass;
- send its own scheduled dashboard.

### The agent may not autonomously

- compose arbitrary recruiter messages;
- negotiate salary;
- accept or reject offers;
- schedule interviews;
- modify applications;
- apply for jobs;
- respond to ambiguous hiring messages;
- bypass confidence, sender, duplicate, or thread guardrails.

The design principle is:

> Grant autonomy only where the cost of a wrong action is low and the action can be constrained by explicit, testable rules.

## 8. Failure Bounds

The system contains explicit runtime and action boundaries.

### Runtime bound

A run has a maximum allowed execution time.

If that ceiling is exceeded, the process terminates instead of hanging indefinitely.

### Confidence bound

External rejection replies require high-confidence semantic classification.

### Action bound

The system has no general-purpose email composition capability in its autonomous workflow.

### Duplicate bound

Persistent message and thread state prevents repeated external actions across runs.

These controls move critical safety decisions out of prompt instructions and into deterministic application logic.

## 9. Architecture Evolution

### V1 — Repeated Historical Processing

The initial implementation repeatedly:

- searched historical Gmail;
- fetched full messages;
- performed semantic classification;
- recalculated different dashboard windows through repeated processing.

Observed runtime:

```text
~10–30 minutes
```

Some executions became stuck significantly longer.

### V2 — Stateful Incremental Processing

The architecture was redesigned to:

- persist classification metadata;
- reuse historical classifications;
- avoid repeated full-message retrieval;
- avoid repeated LLM classification;
- calculate multiple analytics windows from one classified snapshot.

Observed cached runtime:

```text
~9 seconds
```

Compared with a typical 10-minute execution, this represents approximately a:

```text
60× runtime improvement
```

The important architectural change was not merely optimization of individual API calls.

It was the transition from:

```text
recompute historical state every run
```

to:

```text
persist state → process new information → reuse known state
```

## 10. Repository Architecture

```text
job-search-agent/
├── src/
│   ├── job_search.py
│   └── run.py
├── state/                    # runtime only, gitignored
├── docs/
│   ├── ARCHITECTURE.md
│   └── PORTFOLIO.md
├── assets/
│   └── job-search-agent-architecture.png
├── .env.example
├── .gitignore
├── requirements.txt
├── LICENSE
└── README.md
```

The repository separates:

- **product logic** — `src/`;
- **runtime state** — `state/`;
- **scheduler configuration** — `deployment/`;
- **architecture/product documentation** — `docs/`;
- **public assets** — `assets/`.

This keeps the public repository reproducible while excluding credentials, OAuth tokens, personal email state, and other runtime artifacts.