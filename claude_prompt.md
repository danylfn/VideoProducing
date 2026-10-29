# Промпт за Claude (копирайте всичко под линията в нов чат)

Заменете `{{ТЕМА}}` (напр. „Darius – произходът му“) и `{{ЕЗИК}}` (напр. English). Ако Claude има включен web search, точността на лора ще е по-висока. Отговора поставете директно в `script.json` – пайплайнът сам маха ```json огради и излишен текст.

---

You are a scriptwriter for a League of Legends lore YouTube channel. Write a complete production script about: **{{ТЕМА}}**. Narration language: **{{ЕЗИК}}**.

LORE ACCURACY
- Use the current official canon (universe.leagueoflegends.com and official short stories/cinematics). If something was retconned, use the current version. Never invent events, names or quotes. If a detail is uncertain or fan-speculation, frame it as such in the narration ("Some believe...").

STRUCTURE & LENGTH (the video must be ~10 minutes)
- Total narration: 1300–1400 words across all scenes (the voice reads ~136 words per minute including pauses).
- 60–70 scenes. Each scene's narration = 1–2 complete sentences, 15–28 words.
- Narration is pure spoken text: no stage directions, no brackets, no emojis, no abbreviations a voice actor would stumble on.

RETENTION (what YouTube and TikTok reward – follow strictly)
- COLD OPEN (scenes 1–3, first ~20 seconds): start with the single most dramatic, surprising or emotional moment of the whole story, told in the present tense, before any context. Scene 1's first sentence must work as a hook on its own. Never "Welcome back", never "In this video we'll talk about".
- Within the first 30 seconds, promise what the viewer will learn ("…and the reason why will change how you see Noxus").
- OPEN LOOPS: roughly every 60 seconds (every 6–8 scenes) raise a question the story answers later ("But that was not the worst of it.", "He had no idea who was waiting in the capital."). Pay every loop off.
- Escalate: each chapter must raise the stakes compared to the previous one. Cut anything that does not move the story.
- Concrete over abstract: names, places, sensory details and actions, not generic praise ("he was very strong").
- 5–8 chapters. Mark the first scene of each chapter with a "chapter" field (2–5 words, curiosity-driven). Scene 1 must have a chapter.
- Last 2–3 scenes: the emotional payoff that answers the cold open, then ONE short call to action that asks a specific question for the comments (e.g. "Who should we cover next: Draven or Swain?").
- ORIGINALITY (YouTube demonetizes channels whose videos look template-made): give this episode its own angle and thesis (for example "the one decision that defined him" or "the villain's side of the story"), vary the opening style and chapter structure from episode to episode, and avoid stock phrases.

CHARACTERS (visual consistency – critical)
- Define every character who appears in more than one image in "characters": a snake_case key -> ONE noun phrase of 25–45 words starting with "a"/"an": apparent age, build, face and hair, signature armor/clothing with exact colors, weapon or key prop. Purely visual: no names, no lore, no personality.
- Different ages or forms of the same person get separate keys (e.g. darius_young, darius).
- In image_prompt refer to a character ONLY as {key} – e.g. "{darius} standing on a cliff at dawn". The pipeline replaces {key} with the full description. Never write the character's name in an image_prompt.

IMAGE PROMPTS (English, always)
- One concrete visual per scene: subject + action + setting + composition (wide shot / close-up / from behind) + lighting/mood. Max 35 words (character descriptions get inserted on top of that).
- Do NOT add style words (art style, "8k", "highly detailed", artist or studio names) – the pipeline appends a unified style.
- No text, letters, logos or UI in the image.
- Vary shots: alternate wide establishing shots, medium shots and close-ups. Favor silhouettes, shots from behind and props in hands/details for variety.
- Optional "motion": one of zoom_in | zoom_out | pan_left | pan_right (use zoom_in for tense moments, pan for landscapes).

VIDEO PROMPTS (English, always – every scene gets one)
- Each scene's image is animated into a ~10-second video clip. "video_prompt" describes what MOVES in that image, in present tense: the subject's action, motion in the environment (wind, fire, smoke, rain, water, crowds) and the camera movement, ending with the shot type. 35–60 words.
- It must match the image_prompt of the same scene (same subject, setting and framing); refer to characters ONLY as {key}, exactly like image_prompt.
- Prefer motion that video models do well: slow deliberate gestures, cloth/hair/cape in the wind, flames, embers, drifting smoke, rain, marching ranks, slow camera push-ins, pans, arcs and rises. Avoid fast or complex choreography (sword fights, punches, acrobatics), close-up hand interactions and morphing faces.
- No style words (the pipeline appends a unified style), no text, no sound descriptions.

PRONUNCIATIONS (for the text-to-speech voice)
- In "pronunciations", list every unusual proper noun that appears in the narration (champions, places, factions, items) that an English TTS voice might mispronounce. Map the exact spelling used in the narration -> a simple English phonetic respelling with hyphens between syllables, e.g. "Kha'Zix": "Kah-Zix", "Freljord": "Frell-yord". Use the official/in-game pronunciation. No IPA. Skip ordinary English words.

SHORTS (5–6, for YouTube Shorts and TikTok – the biggest source of new viewers)
- Each short = a contiguous scene range (start_scene_id..end_scene_id), 55–100 words of narration (~25–45 seconds), no overlap between shorts. Spread them across the whole story.
- The FIRST sentence of each range is the hook and must grab a stranger in the first second, with no prior context: a shocking fact, a bold claim or a question ("He beheaded his own general – and got promoted.").
- The LAST scene lands a payoff, ideally one that connects back to the first line, so the short loops naturally and gets rewatched.
- Prefer ranges that are self-contained mini-stories with one clear emotion (shock, injustice, triumph, betrayal).
- Short title ≤ 60 characters, curiosity-driven (it is shown on screen during the short). Description: one hook sentence + 3–5 hashtags including #shorts and #leagueoflegends.

YOUTUBE METADATA
- Title ≤ 90 characters, SEO-friendly, includes the champion name and "League of Legends Lore", built on curiosity or conflict, not a plain summary.
- "title_variants": 2 alternative titles with clearly different angles (for YouTube's title A/B test).
- Description: 2–3 hook sentences, then the literal placeholder {{CHAPTERS}} on its own line (the pipeline fills real timestamps – never write timestamps yourself), then 3–5 hashtags. No "<" or ">" characters.
- 10–15 tags.

THUMBNAILS (3 variants for YouTube's thumbnail A/B test – click-through rate decides whether the video is shown at all)
- Each variant has its own "prompt" (an image prompt, English, max 35 words) and "text" (max 4 punchy words, ALL the curiosity, none of the answer).
- The image: ONE subject, close-up or medium close-up, a strong readable emotion (rage, shock, grief, menace), high contrast against a simple dark background, subject on the RIGHT half (the text goes on the left). Refer to characters only as {key}. No text in the image.
- Make the 3 variants genuinely different (different moment, emotion or framing), not three versions of the same picture.

OUTPUT: Return ONLY valid JSON (no comments, no trailing commas) matching exactly this schema:

{
  "project_name": "snake_case_name",
  "characters": {
    "darius": "a towering broad-shouldered warrior in his forties, ...",
    "darius_young": "a gaunt dark-haired boy of about ten, ..."
  },
  "pronunciations": {
    "Basilich": "Bass-ill-itch"
  },
  "youtube_master": {
    "title": "...",
    "title_variants": ["...", "..."],
    "description": "...\n\n{{CHAPTERS}}\n\n#hashtags",
    "tags": ["...", "..."],
    "script": [
      {"scene_id": 1, "chapter": "Chapter Title", "narration": "...", "image_prompt": "{darius} ...", "video_prompt": "{darius} slowly raises ..., the camera slowly pushes in", "motion": "zoom_in"},
      {"scene_id": 2, "narration": "...", "image_prompt": "...", "video_prompt": "..."}
    ]
  },
  "shorts": [
    {"short_id": 1, "title": "...", "description": "... #shorts #leagueoflegends", "start_scene_id": 1, "end_scene_id": 4}
  ],
  "thumbnail": {
    "variants": [
      {"prompt": "extreme close-up of {darius} roaring in rage, ... dark background, subject on the right", "text": "MAX FOUR WORDS"},
      {"prompt": "...", "text": "..."},
      {"prompt": "...", "text": "..."}
    ]
  }
}
