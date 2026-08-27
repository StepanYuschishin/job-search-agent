# Job Search Agent — Portfolio Case Study

## Product Summary

Job Search Agent is a personal agentic AI system designed to reduce repetitive operational work during a high-volume job search.

The system connects to Gmail, identifies recruiting activity, classifies hiring-state emails with an LLM, maintains persistent state, performs narrowly bounded autonomous actions, and sends scheduled analytics dashboards.

The product is built around one central design question:

> Where should AI autonomy stop?

The system therefore separates semantic reasoning from execution authority.

The LLM interprets recruiting messages.

Deterministic application logic decides whether the system is allowed to act.

---

## Problem

A high-volume job search creates repetitive operational overhead:

- checking Gmail for recruiting updates;
- tracking submitted applications;
- tracking rejections;
- identifying interviews and recruiter replies;
- calculating recent funnel activity;
- responding to routine rejection emails;
- avoiding duplicate responses.

The workflow is repetitive, but some actions carry reputational risk.

A wrong dashboard metric is inconvenient.

A wrong recruiter email can damage a real hiring relationship.

This makes the problem suitable for bounded agentic automation rather than unrestricted autonomy.

---

## Product Goal

Reduce repetitive job-search operations while preserving human control over high-impact decisions.

The agent should:

- monitor recruiting activity automatically;
- convert unstructured email into structured hiring state;
- reduce repeated manual tracking;
- perform only low-risk, explicitly permitted actions;
- remain observable and predictable;
- fail safely when confidence or permissions are insufficient.

---

## Core User

The initial user is a job seeker managing a high volume of applications across multiple companies and ATS platforms.

The system is especially useful when the user is receiving enough recruiting email that manual tracking becomes noisy, repetitive, and error-prone.

---

## Core Workflow

```text
Gmail
  ↓
Candidate Email Discovery
  ↓
Persistent State Check
  ↓
LLM Semantic Classification
  ↓
Hiring-State Classification
  ↓
Decision / Guardrail Layer
  ↓
┌─────────────────────┬─────────────────────┐
│ Analytics Path      │ Action Path         │
│                     │                     │
│ Calculate metrics   │ Evaluate rejection  │
│ Build dashboard     │ Check safety rules  │
│ Send dashboard      │ Prevent duplicates  │
│                     │ Send safe reply     │
└─────────────────────┴─────────────────────┘
```

---

## Hiring-State Model

Relevant emails are classified into five states:

- `APPLICATION_CONFIRMATION`
- `REJECTION`
- `INTERVIEW`
- `RECRUITER_REPLY`
- `OTHER`

Each classification includes:

- label;
- confidence;
- semantic reason.

The system uses semantic classification because recruiting messages vary significantly across employers and ATS platforms.

Exact keyword matching alone does not reliably represent the final hiring state.

For example:

```text
"Thank you for applying, but we decided to move forward with another candidate."
```

must resolve to:

```text
REJECTION
```

rather than:

```text
APPLICATION_CONFIRMATION
```

---

## Agentic Behavior

The system is not a chatbot waiting for prompts.

It can execute a recurring operational loop without human initiation:

1. start on schedule;
2. inspect the external environment;
3. retrieve relevant information;
4. reason about hiring state;
5. reuse memory from previous runs;
6. evaluate whether an action is permitted;
7. act when guardrails pass;
8. persist the result;
9. report outcomes.

This makes the product agentic at the workflow level, while its authority remains intentionally constrained.

---

## Autonomy Boundary

The agent may autonomously:

- retrieve recruiting emails;
- classify hiring-state messages;
- persist processing state;
- calculate job-search metrics;
- identify safe rejection replies;
- send a predefined rejection reply when all checks pass;
- send its own scheduled dashboard.

The agent may not autonomously:

- apply for jobs;
- compose arbitrary recruiter messages;
- negotiate salary;
- accept or reject offers;
- schedule interviews;
- modify applications;
- respond to ambiguous recruiter communication;
- override safety rules.

This boundary is deliberate.

Higher-impact decisions remain human-owned.

---

## Rejection Reply Guardrails

Autonomous replies require all conditions to pass.

```text
Classified as REJECTION
        ↓
Confidence >= configured threshold
        ↓
Semantic reason contains rejection evidence
        ↓
Sender is replyable
        ↓
Subject is not blocked
        ↓
Message is not self-sent
        ↓
Message has not already been answered
        ↓
Thread has not already been answered
        ↓
Batch limit has not been exceeded
        ↓
SEND PREDEFINED REPLY
```

If any condition fails:

```text
DO NOT SEND
```

This is one of the key product decisions.

The model can recommend meaning.

It does not directly control external communication.

---

## Persistent Memory

The first implementation repeatedly processed historical Gmail data.

That created unnecessary latency and repeated LLM usage.

The architecture evolved to maintain local persistent state.

### Classification Cache

Stores previously processed email metadata and classifications.

This prevents historical messages from being fetched and classified repeatedly.

### Action Ledger

Stores rejection messages and Gmail thread IDs that have already triggered an autonomous reply.

This prevents duplicate actions across future runs.

---

## Performance Improvement

### Initial architecture

Observed runtime:

```text
~10–30 minutes
```

Some scheduled runs could become stuck significantly longer.

The root problem was repeated historical processing.

### Stateful architecture

After introducing persistent classification state and snapshot reuse:

```text
~9 seconds cached execution
```

Compared with a typical 10-minute execution, this represents approximately:

```text
60× faster
```

The key improvement was architectural rather than purely computational.

The system moved from:

```text
recalculate historical state every run
```

to:

```text
reuse known state + process new information
```

---

## Production Snapshot

One August 2026 production snapshot included:

- **387 applications detected**
- **147 rejections detected**
- scheduled dashboard delivery
- semantic recruiting-email classification
- autonomous rejection-reply workflow
- persistent classification state
- message-level duplicate protection
- thread-level duplicate protection
- approximately **9-second cached execution**

These metrics reflect a point-in-time real-world run of the system.

---

## Key Product Decisions

### 1. Semantic classification over keyword-only logic

Recruiting communication is inconsistent across employers and ATS platforms.

The system therefore uses an LLM to interpret meaning rather than treating phrases as deterministic states.

### 2. Deterministic controls around probabilistic reasoning

The LLM classifies.

Application logic authorizes.

This prevents model output from becoming direct execution authority.

### 3. Predefined replies instead of free-form generation

Autonomous communication is constrained to a known, low-risk response.

This reduces reputational risk.

### 4. Persistent state over stateless rescanning

Previously processed information is reused rather than repeatedly recomputed.

This improves speed, reliability, and cost.

### 5. Thread-level idempotency

Duplicate protection operates at both message and conversation level.

This prevents repeated replies inside the same recruiter thread.

### 6. Runtime ceiling

Scheduled executions have a maximum allowed runtime.

A stuck process terminates instead of remaining active indefinitely.

---

## Product Architecture

The implementation separates responsibility across four layers:

### Environment

Gmail

### Reasoning

OpenAI semantic classification

### State

Local JSON classification cache and action ledger

### Deterministic Control

Confidence thresholds, sender rules, subject rules, duplicate protection, batch limits, and runtime bounds

This architecture allows AI reasoning without delegating unrestricted control.

---

## Technology

- Python
- OpenAI API
- Gmail API
- OAuth 2.0
- JSON local state
- macOS launchd
- Git
- GitHub

---

## What I Learned

The most important lesson was that agent quality is not only about model intelligence.

A useful autonomous product also requires:

- explicit authority boundaries;
- persistent state;
- idempotency;
- deterministic safety controls;
- observability;
- runtime limits;
- failure handling;
- separation between reasoning and execution.

The strongest architectural improvement was moving from a stateless repeated-computation workflow to a stateful incremental system.

The strongest product decision was refusing to treat semantic confidence as sufficient permission for external action.

---

## Current Scope

The current product focuses on:

```text
Recruiting Email
        ↓
Hiring-State Classification
        ↓
Persistent State
        ↓
Bounded Autonomous Action
        ↓
Job-Search Analytics
```

Potential future extensions include:

- structured opportunity tracking;
- recruiter follow-up recommendations;
- application-source integrations;
- hiring-funnel analytics;
- human-approved higher-impact actions;
- configurable policies for different users.

The autonomy boundary should expand only when additional actions can be evaluated and constrained with comparable reliability.