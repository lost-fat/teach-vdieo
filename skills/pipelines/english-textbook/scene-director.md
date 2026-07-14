# Scene Director — English Textbook Pipeline

## When to Use

Use after narration timing exists to turn its visual beats into a feasible, coherent `scene_plan`.

## Process

1. Treat `narration_timeline` time ranges as binding.
2. Prefer one coherent ten-second image-to-video shot with one or two internal visual beats rather than disconnected clips.
3. Write prompts with the `esl-cinematic-editorial` anchors: mature semi-realistic editorial treatment, natural light, stable geography and period detail.
4. Keep all readable text out of generated media; captions are a composition layer.
5. Plan a 16:9 first frame that keeps the lower title-safe band clear and specifies real subject/camera motion for Wan.
6. Validate `scene_plan`, review feasibility, and checkpoint it.

## Self-Evaluate

- Every beat starts and ends within measured narration/video duration.
- The visual idea reads as one continuous scene.
- Prompt intent contains no request for rendered words.
- Required assets name the locked image and image-to-video path.

## Common Pitfalls

- Forcing one video clip per narration fragment.
- Asking the image model to render captions or maps with labels.
- Losing identity or historical continuity between beats.
