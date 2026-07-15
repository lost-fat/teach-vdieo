# Scene Director — English Textbook Pipeline

## When to Use

Use after narration timing exists to turn a short passage or complete article into a coherent, provider-feasible `scene_plan`.

## Process

1. Treat measured `narration_timeline` ranges as binding and derive ordered `narrative_units` with discourse roles such as setting, movement, comparison, cause/effect, process, historical event, abstract concept, dialogue, or transition.
2. Build one article-level `continuity_bible` before individual scenes: canonical entities and locations, approved translations, period facts, immutable visual traits, palette, lighting, texture, camera rules, and prohibited elements. Refer to these entries by stable IDs instead of rewriting them per prompt.
3. Group adjacent narrative units into the same scene while subject, place, time, and visual logic remain continuous. Start a new generated clip only for a real context change or when the provider clip limit would be exceeded. Subtitle pages never create scene boundaries.
4. Select a visual treatment from the discourse role: establish a setting, follow movement, show a before/after or parallel comparison, make cause and effect visibly unfold, reveal a process step-by-step, reconstruct a historical moment, use a grounded visual metaphor for an abstract concept, or stage dialogue with stable screen direction.
5. Author a provider-neutral `video_prompt_spec` for each scene: single-shot intent, subject motion, camera motion, timed action beats, continuity references, optional foreground parallax event, optional visual payoff, caption-safe area, and negative constraints. Temporal beats use scene-local time from `0` to the scene duration, not absolute article time. Motion should develop over time rather than animate a still frame in place.
6. Keep readable text out of generated media; captions and teaching labels belong to Remotion.
7. Use the `esl-cinematic-editorial` anchors: mature semi-realistic editorial treatment, natural light, stable geography and period detail. Plan each 16:9 first frame with the lower caption band visually quiet.
8. Validate `scene_plan`, review feasibility, and checkpoint it.

## Self-Evaluate

- Every beat starts and ends within measured narration/video duration and the current provider limit.
- Each scene may cover one or more adjacent narrative units without sentence-per-clip fragmentation.
- Prompt intent contains no request for rendered words.
- Continuity references resolve to the article-level bible and required assets name the locked image and image-to-video path.

## Common Pitfalls

- Forcing one video clip per narration fragment.
- Asking the image model to render captions or maps with labels.
- Repeating the same generic camera move for unrelated discourse roles.
- Losing identity or historical continuity between scenes.
