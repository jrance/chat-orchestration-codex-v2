# GroupChat Orchestration

The **groupchat** orchestration node convenes a short, guided discussion between multiple child agents. A language-model moderator selects the next speaker, enforces brevity, evaluates stop conditions, and optionally delivers the final synthesis back to the user. Per-turn outputs are emitted to telemetry for observability, while the user only sees the moderator's concluding answer (or the selected participant answer when synthesis is disabled).

## Configuration

| Field | Description | Default |
| --- | --- | --- |
| `moderatorPrompt` | Moderator system guidance. Include role rules, tone, and how `{participants}` should collaborate. | **required** |
| `maxTurns` | Maximum participant turns before forcing a stop. Includes only participant utterances. | `6` |
| `stopWhen` | Stop rule. `ModeratorSatisfied` asks for explicit approval. `AllAgree` requires consensus. | `ModeratorSatisfied` |
| `emitSynthesis` | When `true`, moderator produces the final answer; when `false`, the best participant response is returned. | `true` |
| `speakerBudgetTokens` | Soft token budget per participant reply. Forwarded as moderator guidance. | `400` |

## Example IR

```json
{
  "meta": { "id": "pkg", "name": "Policy Roundtable", "version": "1.0.0" },
  "entryId": "groupchat",
  "nodes": [
    {
      "id": "groupchat",
      "kind": "groupchat",
      "label": "Policy Council",
      "data": {
        "moderatorPrompt": "Coordinate domain experts, stay concise, and stop when you are satisfied.",
        "maxTurns": 4,
        "stopWhen": "ModeratorSatisfied",
        "speakerBudgetTokens": 120
      }
    },
    { "id": "security", "kind": "agent.codeless", "label": "Security Advisor", "data": { /* ... */ } },
    { "id": "compliance", "kind": "agent.codeless", "label": "Compliance Lead", "data": { /* ... */ } }
  ],
  "edges": [
    { "id": "gc-1", "from": "groupchat", "to": "security" },
    { "id": "gc-2", "from": "groupchat", "to": "compliance" }
  ]
}
```

## Execution Flow

1. Moderator inspects the recent transcript and issues the next-speaker decision via `choose_next_speaker`.
2. The chosen participant agent runs once (bounded by `speakerBudgetTokens`). Its tokens are streamed to telemetry with speaker labels.
3. Transcript is updated and stop conditions are evaluated:
   - `ModeratorSatisfied` &rarr; call `is_satisfied`.
   - `AllAgree` &rarr; call `has_consensus`.
4. Loop repeats up to `maxTurns`. When finished:
   - If `emitSynthesis = true`, moderator produces a final synthesis (`generate_synthesis`).
   - Otherwise the best participant answer is returned (latest successful turn).

The resulting metadata is stored on `scratch.groupchat[nodeId]` and mirrored on `response.metadata.groupchat`.

## Moderator Prompt Tips

Include these contextual tokens in `moderatorPrompt` to tailor guidance:

- `{participants}` – comma-separated labels (e.g. "Security Advisor, Compliance Lead").
- `{rules}` – any organization-wide guardrails.
- `{user_question}` – the latest user request or task.
- `{now_iso}` / `{date_yyyy_mm_dd}` – current timestamp hints.
- `{user_details}` – JSON-encoded requester information (department, locale, etc.).

Example snippet:

```
Moderate a focused discussion. {participants} must avoid repeating content. Summaries should reference policy IDs.
We must follow {rules}. The user asked: {user_question}.
```

## Streaming Behaviour

- **Telemetry stream**: Receives `telemetry.groupchat.turn.delta` events for every participant token burst, plus `telemetry.groupchat.turn.completed` summaries (usage, status, speaker).
- **Main Responses stream**: Emits exactly one `response.output_text.delta` (the moderator synthesis or chosen participant answer), followed by `response.completed` with metadata:

```json
{
  "groupchat": {
    "turns": 3,
    "stopWhen": "ModeratorSatisfied",
    "finalSpeaker": "Moderator",
    "participants": ["Security Advisor", "Compliance Lead"],
    "stopReason": "ModeratorSatisfied"
  }
}
```

## Troubleshooting

- **Oscillating speakers**: Ensure the moderator prompt explicitly encourages new information and penalises repetition. Consider lowering `maxTurns`.
- **Consensus never reached** (`AllAgree`): Prime participants to state their position clearly, and tighten the `has_consensus` rubric.
- **No final synthesis**: If `emitSynthesis=true` but the moderator returns an empty answer, verify the prompt includes `{user_question}` and a directive to deliver a final response.
- **Long participant replies**: Reduce `speakerBudgetTokens` or add explicit instructions such as "respond in two sentences".

Groupchat nodes remain extensible: additional moderator policies or participant sources can be registered via the existing provider helper hooks.
