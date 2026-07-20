# Lesson Studio Storyboard Preview Contract

Read the complete source article before designing any shot. Return one JSON
object with the exact shape below. This is a whole-article visual story, not a
sentence-by-sentence illustration exercise.

Requirements:

- Use 3–12 scenes. Each scene is one continuous 14-second generated clip.
- `source_text` 必须保留英文原文，不得翻译或改写。
- 除 `source_text` 外，其余所有文本字段必须使用简体中文。字段枚举值仍使用下方指定的英文代码。
- `source_text` values, joined with one space, must reproduce the complete
  source article exactly, in order, with no omission, rewriting, or overlap.
- Give the story a grounded recurring carrier. Its changing state must carry
  the article from opening through turning point to payoff.
- Decide human presence from the article instead of banning or forcing people.
  If the source mentions passengers, workers, residents, families, customers,
  or other people, at least one scene must show geographically and historically
  appropriate people doing a natural consequential action. An object carrier
  may connect the story, but it must not erase human agency.
- Use `human_presence: none` only when people are genuinely unnecessary. Avoid
  posed portraits and talking heads; prefer observed actions in context.
- Use at least three different `story_beat` values and a recognizable opening,
  turning point, and closing state.
- The image description is a first frame, not a list of spoken nouns.
- The three temporal actions must form one continuous shot with visible
  progression, foreground parallax, and an earned visual payoff.
- Do not request generated text, subtitles, labels, split screens, maps with
  labels, talking heads, lip sync, internal cuts, fades, or morph transitions.
- Keep geography, period, characters, props, screen direction, palette, and
  lighting consistent across adjacent scenes.

Allowed values:

- `carrier.kind`: person, object, place, process, question, motif, ensemble
- `story_beat`: hook, setup, tension, turning_point, development, payoff, reflection
- `visual_role`: setting, movement, comparison, cause_effect, process,
  historical_event, abstract_concept, dialogue, transition
- `visual_mode`: direct_evidence, interpretive, metaphor, bridge, payoff

JSON shape:

```json
{
  "theme": "one sentence",
  "visual_premise": "one sentence",
  "carrier": {
    "kind": "object",
    "name": "canonical short name",
    "description": "why this carrier can sustain the whole article",
    "traits": ["stable visible trait", "stable visible trait"]
  },
  "opening_state": "observable opening state",
  "turning_point": "observable turning point",
  "closing_state": "observable resolved state",
  "recurring_motif": "matchable shape, action, direction, or prop",
  "style": {
    "palette": ["color family", "color family"],
    "lighting": "stable natural-light rule",
    "texture": "mature editorial visual texture"
  },
  "scenes": [
    {
      "source_text": "exact contiguous source excerpt",
      "visual_role": "historical_event",
      "story_beat": "setup",
      "chapter_objective": "what this chapter must change",
      "story_contribution": "how this shot advances the whole visual story",
      "visual_mode": "interpretive",
      "description": "specific cinematic first frame",
      "state_from": "observable state before this shot",
      "state_to": "observable state after this shot",
      "subject_motion": "consequential subject action",
      "camera_motion": "camera response to that action",
      "temporal_actions": [
        "0–5 second action",
        "5–10 second action",
        "10–14 second action and payoff"
      ],
      "foreground_event": "foreground parallax event",
      "visual_payoff": "final visible payoff",
      "match_action": "continuity bridge from the previous scene",
      "human_presence": "none | background | supporting | primary",
      "human_action": "简体中文的人物自然行动；human_presence 为 none 时使用空字符串"
    }
  ]
}
```
