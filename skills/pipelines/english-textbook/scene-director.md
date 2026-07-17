# Scene Director — English Textbook Pipeline

## When to Use

Use after narration timing exists to turn a short passage or complete article into a coherent, provider-feasible `scene_plan`.

## Process

1. Treat measured `narration_timeline` ranges as binding and derive ordered `narrative_units` with discourse roles such as setting, movement, comparison, cause/effect, process, historical event, abstract concept, dialogue, or transition. **Narrative units are evidence, not shot requests.** A new sentence, quotation, subtitle page, or vocabulary item does not by itself justify a new picture or clip.
2. **Pass A — whole-article dramaturgy.** Read the complete article before designing any shot. Extract its causal or emotional transformation, then write a `visual_story_arc` with one theme, one visual premise, an opening state, a turning point, a closing state, and a recurring motif. Choose a **story carrier** — a recurring person, object, place, process, question, motif, or ensemble — whose changing state lets viewers feel that transformation. The carrier must be grounded in the article, but it need not literally depict every noun being spoken.
3. Divide the article into visual chapters such as hook, setup, tension, turning point, development, payoff, and reflection. Each chapter groups one or more adjacent narrative units around a dramatic objective and an entry-to-exit state change. A chapter is a story unit, not a provider clip: it may remain visually continuous across several narration sentences and may span several technical clips.
4. Build one article-level `continuity_bible` before individual scenes: canonical entities and locations, approved translations, period facts, immutable visual traits, palette, lighting, texture, camera rules, and prohibited elements. Include the story carrier or its stable visual motif when it must recur. Refer to entries by stable IDs instead of rewriting them per prompt.
5. **Pass B — shot design.** Design only the shots needed to advance the chapter state. For every scene, record `story_chapter_id`, `story_beat`, `story_contribution`, `visual_mode`, and a concrete `visual_state_change` from one observable state to another. Use direct evidence for indispensable facts; use grounded interpretive action, cause-and-effect, recurring motifs, visual bridges, and human-scale payoffs for exposition or testimony. Never paraphrase the narration into a list of pictured nouns.
6. Group adjacent narrative units into the same scene while the carrier, place, time, screen direction, and visual objective remain continuous. Start a new creative shot only for a meaningful state or context change. If the provider duration limit alone forces a boundary, keep the same chapter and carrier, set `continuity_from_scene_id`, and specify a match action, shape, direction, or motif so the next clip feels like a continuation rather than a reset.
7. Author a provider-neutral `video_prompt_spec` for each generated scene. Give the subject something consequential to do, include an obstacle, reveal, cause-and-effect change, foreground event, or visual payoff where appropriate, and make the camera respond to that action. Temporal beats use scene-local time from `0` to the scene duration, not absolute article time. Avoid a sequence of generic dolly-ins or repeated establishing shots; motion must develop the story rather than merely animate a still frame.
8. Keep readable text out of generated media; captions and teaching labels belong to Remotion. Use the `esl-cinematic-editorial` anchors: mature semi-realistic editorial treatment, natural light, stable geography and period detail. Plan each 16:9 first frame with the lower caption band visually quiet.
9. Validate `scene_plan`, review feasibility, and checkpoint it.

## Self-Evaluate

- Every beat starts and ends within measured narration/video duration and the current provider limit.
- The visual story has a recognizable opening state, turning point, and payoff carried across chapters; scene descriptions are not sentence paraphrases.
- Each scene advances an observable state and may cover one or more adjacent narrative units without sentence-per-clip fragmentation.
- Prompt intent contains no request for rendered words.
- Continuity references resolve to the article-level bible and required assets name the locked image and image-to-video path.

## Common Pitfalls

- Forcing one video clip per narration fragment.
- Choosing a fresh subject for each sentence instead of carrying one dramatic premise forward.
- Treating the provider clip limit as a reason to restart the composition rather than continue with a match action.
- Asking the image model to render captions or maps with labels.
- Repeating the same generic camera move for unrelated discourse roles.
- Losing identity or historical continuity between scenes.
